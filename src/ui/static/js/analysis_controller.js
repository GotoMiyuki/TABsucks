/** analysis controller: shared state, explicit host callbacks. */
import api from './api.js?v=20260926p2';
import { state, TRACKS, TRACK_LABELS, TRACK_COLORS } from './app_state.js?v=20260926p2';

export function createAnalysisController({ showToast, updateNavigationControls }) {
    function onAnalysisStarted(payload = {}) {
        const track = payload.track;
        if (!track) return;
        if (payload.task_id) state.analysisTaskIds[track] = payload.task_id;
        if (payload.plugin) {
            state.analysisPendingPlugins[track] = payload.plugin;
        }
        state.analysisRunning.add(track);
        updateAnalysisCardState(track, 'running');
        updateAnalysisCompletionCount();
        updateNavigationControls();
        showToast(`分析 ${track} 已开始...`, 'info');
    }

    function onAnalysisProgress(track, progress, taskId) {
        if (taskId && state.analysisTaskIds[track] && taskId !== state.analysisTaskIds[track]) return;
        const card = document.querySelector(`.analysis-track-card[data-track="${track}"]`);
        if (!card) return;
        const status = card.querySelector('.analysis-status');
        if (status) {
            status.textContent = `${Math.round((progress || 0) * 100)}%`;
            status.className = 'analysis-status running';
        }
    }

    function normalizeAnalysisResult(result) {
        if (Array.isArray(result)) return { chords: result };
        if (result && typeof result === 'object') return result;
        return null;
    }

    function onAnalysisDone(payload = {}) {
        const track = payload.track;
        if (!track) return;
        if (payload.task_id && state.analysisTaskIds[track] && payload.task_id !== state.analysisTaskIds[track]) return;
        state.analysisRunning.delete(track);
        delete state.analysisTaskIds[track];
        const plugin = payload.plugin || state.analysisPendingPlugins[track];
        delete state.analysisPendingPlugins[track];
        const result = normalizeAnalysisResult(payload.result);
        if (result && plugin) {
            state.analysisResults[track] = result;
            state.analysisResultPlugins[track] = plugin;
        }
        updateAnalysisCardState(
            track,
            isTrackAnalysisComplete(track) ? 'done' : 'idle'
        );
        updateAnalysisCompletionCount();
        updateNavigationControls();
        showToast(`分析 ${track} 完成`, 'success');
        advanceAnalysisBatch(track);
    }

    function onAnalysisFailed(payload = {}) {
        const track = payload.track;
        if (!track) return;
        if (payload.task_id && state.analysisTaskIds[track] && payload.task_id !== state.analysisTaskIds[track]) return;
        state.analysisRunning.delete(track);
        delete state.analysisTaskIds[track];
        delete state.analysisPendingPlugins[track];
        updateAnalysisCardState(track, 'idle');
        updateAnalysisCompletionCount();
        updateNavigationControls();
        showToast(`分析 ${track} 失败: ${payload.error || '未知错误'}`, 'error');
        advanceAnalysisBatch(track);
    }

    function onAnalysisCancelled(payload = {}) {
        const track = payload.track;
        if (!track) return;
        if (payload.task_id && state.analysisTaskIds[track] && payload.task_id !== state.analysisTaskIds[track]) return;
        state.analysisRunning.delete(track);
        delete state.analysisTaskIds[track];
        cancelAnalysisBatch();
        updateAnalysisCardState(track, 'idle');
        showToast(`分析 ${track} 已取消`, 'info');
    }

    // ══════════════════════════════════════
    //  Tab3 — Analysis config + results
    // ══════════════════════════════════════

    let _analyzerPlugins = [];

    async function loadAnalyzerPlugins() {
        const r = await api.listAnalyzerPlugins();
        if (r.ok) {
            const plugins = Array.isArray(r) ? r : (r.data || []);
            _analyzerPlugins = plugins.filter(
                plugin => typeof plugin.name === 'string'
                    && plugin.name.startsWith('chord_')
            );
        }
        return _analyzerPlugins;
    }

    function compatibleAnalyzers(track) {
        return _analyzerPlugins.filter(plugin =>
            Array.isArray(plugin.input_stems)
            && plugin.input_stems.includes(track)
        );
    }

    function initializeAnalyzerSelections() {
        for (const track of TRACKS) {
            if (!state.selectedTracks.has(track)) continue;
            const compatible = compatibleAnalyzers(track);
            const resultPlugin = state.analysisResultPlugins[track];
            const preferred = compatible.some(p => p.name === resultPlugin)
                ? resultPlugin
                : compatible[0]?.name;
            if (preferred) state.analyzerSelections[track] = preferred;
        }
    }

    function isTrackAnalysisComplete(track) {
        const selectedPlugin = state.analyzerSelections[track];
        return !!selectedPlugin
            && !!state.analysisResults[track]
            && state.analysisResultPlugins[track] === selectedPlugin
            && !state.analysisRunning.has(track);
    }

    function renderAnalysisConfig() {
        const container = document.getElementById('analysis-config-list');
        if (!container) return;

        const tracks = TRACKS.filter(track => state.selectedTracks.has(track));
        updateAnalysisCompletionCount();
        const runAll = document.getElementById('btn-run-all');
        if (runAll) {
            const hasRunnable = tracks.some(track =>
                compatibleAnalyzers(track).length > 0
                && !isTrackAnalysisComplete(track)
            );
            runAll.disabled = !hasRunnable
                || state.analysisRunning.size > 0
                || state.analysisBatchRunning;
            runAll.textContent = state.analysisBatchRunning
                ? 'running batch...'
                : 'run all selected';
        }
        if (tracks.length === 0) {
            container.innerHTML = '<p class="empty-msg compact">请先在 Tab2 选择音轨</p>';
            return;
        }
        if (_analyzerPlugins.length === 0) {
            container.innerHTML = '<p class="empty-msg">no analyzer plugins available</p>';
            return;
        }

        container.innerHTML = tracks.map(track => {
            const compatible = compatibleAnalyzers(track);
            const selected = state.analyzerSelections[track];
            const selectedIsCompatible = compatible.some(p => p.name === selected);
            if (!selectedIsCompatible && compatible.length > 0) {
                state.analyzerSelections[track] = compatible[0].name;
            }
            const activePlugin = state.analyzerSelections[track];
            const opts = compatible.map(p =>
                `<option value="${p.name}" ${p.name === activePlugin ? 'selected' : ''}>
                    ${p.display_name || p.name}
                </option>`
            ).join('');
            const running = state.analysisRunning.has(track);
            const done = isTrackAnalysisComplete(track);
            const unsupported = compatible.length === 0;
            const queued = state.analysisBatchQueue.some(
                item => item.track === track
            );
            const controlsDisabled = running
                || unsupported
                || state.analysisBatchRunning;
            const statusCls = running ? 'running' : (done ? 'done' : '');
            return `
                <div class="analysis-track-card" data-track="${track}">
                    <span class="track-label" style="color:${TRACK_COLORS[track] || '#fff'}">
                        ${TRACK_LABELS[track] || track}
                    </span>
                    <select class="sel-analyzer" data-track="${track}" ${controlsDisabled ? 'disabled' : ''}>
                        ${unsupported ? '<option value="">no compatible chord analyzer</option>' : opts}
                    </select>
                    <button class="btn-pill-sm btn-run-analysis" data-track="${track}" ${controlsDisabled ? 'disabled' : ''}>
                        ${unsupported ? 'unavailable' : (running ? 'running...' : (queued ? 'queued' : (done ? 're-run' : 'run')))}
                    </button>
                    <span class="analysis-status ${unsupported ? 'unsupported' : (queued ? 'queued' : statusCls)}">
                        ${unsupported ? 'unsupported' : (running ? '···' : (queued ? 'queued' : (done ? 'done' : '')))}
                    </span>
                </div>`;
        }).join('');

        container.querySelectorAll('.sel-analyzer').forEach(sel => {
            sel.addEventListener('change', () => {
                state.analyzerSelections[sel.dataset.track] = sel.value;
                renderAnalysisConfig();
                updateNavigationControls();
            });
        });
        container.querySelectorAll('.btn-run-analysis').forEach(btn => {
            btn.addEventListener('click', () => handleRunAnalysis(btn.dataset.track));
        });
    }

    async function handleRunAnalysis(track, pluginOverride = null) {
        if (!state.currentWid) return false;
        if (state.analysisRunning.has(track)) return false;

        const sel = document.querySelector(`.sel-analyzer[data-track="${track}"]`);
        const plugin = pluginOverride || sel?.value;
        if (!plugin) {
            showToast(`请先为 ${track} 选择分析工具`, 'warning');
            return false;
        }

        const wid = state.currentWid;
        state.analyzerSelections[track] = plugin;
        state.analysisPendingPlugins[track] = plugin;
        delete state.analysisResults[track];
        delete state.analysisResultPlugins[track];
        state.analysisRunning.add(track);
        updateAnalysisCardState(track, 'running');
        updateNavigationControls();

        const r = await api.analyze(wid, track, plugin);
        if (r.ok) state.analysisTaskIds[track] = r.task_id || null;
        if (state.currentWid !== wid) return false;
        if (!r.ok) {
            state.analysisRunning.delete(track);
            delete state.analysisPendingPlugins[track];
            updateAnalysisCardState(track, 'idle');
            showToast(`启动分析失败: ${r.error}`, 'error');
            return false;
        }
        return true;
    }

    function updateAnalysisCardState(track, st) {
        const card = document.querySelector(`.analysis-track-card[data-track="${track}"]`);
        if (!card) return;
        const btn = card.querySelector('.btn-run-analysis');
        const status = card.querySelector('.analysis-status');
        const sel = card.querySelector('.sel-analyzer');

        if (st === 'running') {
            if (btn) { btn.textContent = 'running...'; btn.disabled = true; }
            if (sel) sel.disabled = true;
            if (status) { status.textContent = '···'; status.className = 'analysis-status running'; }
        } else if (st === 'done') {
            if (btn) { btn.textContent = 're-run'; btn.disabled = false; }
            if (sel) sel.disabled = false;
            if (status) { status.textContent = 'done'; status.className = 'analysis-status done'; }
        } else {
            if (btn) { btn.textContent = 'run'; btn.disabled = false; }
            if (sel) sel.disabled = false;
            if (status) { status.textContent = ''; status.className = 'analysis-status'; }
        }
        updateAnalysisCompletionCount();
        updateNavigationControls();
    }

    function updateAnalysisCompletionCount() {
        const selected = TRACKS.filter(track => state.selectedTracks.has(track));
        const completed = selected.filter(track => isTrackAnalysisComplete(track)).length;
        const count = document.getElementById('analysis-complete-count');
        if (count) count.textContent = `${completed} / ${selected.length}`;
    }

    function cancelAnalysisBatch() {
        state.analysisBatchQueue = [];
        state.analysisBatchCurrent = null;
        state.analysisBatchRunning = false;
    }

    function advanceAnalysisBatch(track) {
        if (
            !state.analysisBatchRunning
            || state.analysisBatchCurrent !== track
        ) {
            return;
        }
        state.analysisBatchCurrent = null;
        void startNextBatchAnalysis();
    }

    async function startNextBatchAnalysis() {
        if (
            !state.analysisBatchRunning
            || state.analysisBatchCurrent !== null
        ) {
            return;
        }

        const next = state.analysisBatchQueue.shift();
        if (!next) {
            state.analysisBatchRunning = false;
            renderAnalysisConfig();
            updateNavigationControls();
            showToast('所有已选音轨分析完成', 'success');
            return;
        }

        state.analysisBatchCurrent = next.track;
        renderAnalysisConfig();
        const launched = await handleRunAnalysis(next.track, next.plugin);
        if (!launched && state.analysisBatchCurrent === next.track) {
            state.analysisBatchCurrent = null;
            void startNextBatchAnalysis();
        }
    }

    async function handleRunAllAnalyses() {
        if (!state.currentWid || state.analysisBatchRunning) return;
        const cards = [...document.querySelectorAll('.analysis-track-card')];
        state.analysisBatchQueue = cards.flatMap(card => {
            const track = card.dataset.track;
            const plugin = card.querySelector('.sel-analyzer')?.value;
            if (
                !plugin
                || state.analysisRunning.has(track)
                || isTrackAnalysisComplete(track)
            ) {
                return [];
            }
            return [{ track, plugin }];
        });
        if (state.analysisBatchQueue.length === 0) return;

        state.analysisBatchRunning = true;
        state.analysisBatchCurrent = null;
        renderAnalysisConfig();
        updateNavigationControls();
        showToast('已按顺序启动批量分析', 'info');
        await startNextBatchAnalysis();
    }

    // ══════════════════════════════════════
    //  Tab4 — Playback / visualization
    // ══════════════════════════════════════
    return { onAnalysisStarted, onAnalysisProgress, onAnalysisDone, onAnalysisFailed, onAnalysisCancelled, loadAnalyzerPlugins, initializeAnalyzerSelections, isTrackAnalysisComplete, renderAnalysisConfig, cancelAnalysisBatch, handleRunAllAnalyses };
}
