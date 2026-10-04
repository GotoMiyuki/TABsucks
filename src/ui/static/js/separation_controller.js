/** Separation task progress and stem selection. */
import api from './api.js?v=20260926p2';
import { state, TRACKS, TRACK_LABELS, TRACK_COLORS } from './app_state.js?v=20260926p2';

export function createSeparationController({ showToast, updateNavigationControls, cancelAnalysisBatch, handleRunAllAnalyses, renderAnalysisConfig, loadAnalyzerPlugins }) {
    function bindStep2() {
        // 下拉填充模型列表（进入 Tab2 时拉一次）
        const sel = document.getElementById('sel-separator');
        if (sel) {
            (async () => {
                const r = await api.listSeparatorPlugins();
                if (!r.ok) { sel.innerHTML = '<option value="">— 加载失败 —</option>'; return; }
                const list = r.data || r;
                sel.innerHTML = list.map(p =>
                    `<option value="${p.name}">${p.display_name}</option>`
                ).join('') || '<option value="">— 无可用模型 —</option>';
            })();
        }

        document.getElementById('btn-start-sep')?.addEventListener('click', triggerSeparation);
        document.getElementById('btn-cancel-sep')?.addEventListener('click', async () => {
            if (!state.separationTaskId) return;
            const result = await api.cancelTask(state.separationTaskId);
            showToast(result.ok ? '正在取消分离，等待模型退出' : `取消失败: ${result.error}`, result.ok ? 'info' : 'error');
        });
        document.getElementById('btn-cancel-analysis')?.addEventListener('click', async () => {
            const taskId = Object.values(state.analysisTaskIds).find(Boolean);
            cancelAnalysisBatch();
            if (!taskId) return;
            const result = await api.cancelTask(taskId);
            showToast(result.ok ? '正在取消分析，等待模型退出' : `取消失败: ${result.error}`, result.ok ? 'info' : 'error');
        });
        document.getElementById('btn-run-all')?.addEventListener('click', handleRunAllAnalyses);
    }

    async function triggerSeparation() {
        if (!state.currentWid) {
            showToast('请先创建车间', 'warning');
            return;
        }
        const sel = document.getElementById('sel-separator');
        const model = sel?.value;
        const device = document.querySelector('input[name="sep-device"]:checked')?.value || 'gpu';
        if (!model) {
            showToast('请先在下拉列表中选择分离模型', 'warning');
            return;
        }
        const wid = state.currentWid;
        const previous = {
            separated: state.separated,
            availableTracks: [...state.availableTracks],
            selectedTracks: new Set(state.selectedTracks),
            analysisResults: { ...state.analysisResults },
            analysisResultPlugins: { ...state.analysisResultPlugins },
            analyzerSelections: { ...state.analyzerSelections },
            analysisPendingPlugins: { ...state.analysisPendingPlugins },
        };
        state.separating = true;
        document.getElementById('btn-cancel-sep')?.classList.remove('hidden');
        state.separated = false;
        state.availableTracks = [];
        state.selectedTracks.clear();
        state.analysisResults = {};
        state.analysisResultPlugins = {};
        state.analyzerSelections = {};
        state.analysisPendingPlugins = {};
        state.analysisRunning.clear();
        cancelAnalysisBatch();
        renderStemSelection();
        renderAnalysisConfig();
        updateNavigationControls();
        // 显示进度环
        document.getElementById('sep-ring-wrap-2')?.classList.remove('hidden');
        const r = await api.separate(wid, model, device);
        if (r.ok) state.separationTaskId = r.task_id || null;
        if (state.currentWid !== wid) return;
        if (!r.ok) {
            state.separating = false;
            document.getElementById('btn-cancel-sep')?.classList.add('hidden');
            state.separated = previous.separated;
            state.availableTracks = previous.availableTracks;
            state.selectedTracks = previous.selectedTracks;
            state.analysisResults = previous.analysisResults;
            state.analysisResultPlugins = previous.analysisResultPlugins;
            state.analyzerSelections = previous.analyzerSelections;
            state.analysisPendingPlugins = previous.analysisPendingPlugins;
            renderStemSelection();
            renderAnalysisConfig();
            updateNavigationControls();
            showToast(`启动分离失败: ${r.error}`, 'error');
            return;
        }
        showToast('分离任务已启动，等待结果...', 'info');
    }

    function updateDlProgress(p) {
        const bar = document.getElementById('dl-progress-bar');
        const text = document.getElementById('dl-progress-text');
        const wrap = document.getElementById('dl-progress-wrap');
        if (bar) bar.value = Math.round(p * 100);
        if (text) text.textContent = `${Math.round(p * 100)}%`;
        if (wrap) wrap.classList.remove('hidden');
    }

    function updateSepProgress(p) {
        // 同时更新 Tab1 和 Tab2 的进度环（避免 DOM 重复 id 问题）
        for (const suffix of ['', '-2']) {
            const ring = document.getElementById(`sep-ring-fg${suffix}`);
            const label = document.getElementById(`sep-ring-label${suffix}`);
            if (ring) ring.setAttribute('stroke-dashoffset', String(120 - 120 * p));
            if (label) label.textContent = `${(p * 100).toFixed(0)}%`;
            if (ring) {
                const wrap = ring.closest('.sep-ring-wrap');
                if (wrap) wrap.classList.remove('hidden');
            }
        }
    }

    async function onSeparationDone(payload = {}) {
        if (payload.task_id && state.separationTaskId && payload.task_id !== state.separationTaskId) return;
        const wid = state.currentWid;
        let tracks = Array.isArray(payload.tracks) ? payload.tracks : [];
        if (tracks.length === 0 && wid) {
            const response = await api.getWorkshopState(wid);
            if (state.currentWid !== wid) return;
            if (response.ok) {
                const workshopState = response.data || response;
                if (workshopState.TabState?.Tab2?.SeparationState !== 'done') {
                    return;
                }
                const paths = workshopState.TabState?.Tab2?.TrackAudioFilePath || {};
                tracks = Object.keys(paths);
            }
        }
        const missingTracks = TRACKS.filter(track => !tracks.includes(track));
        if (missingTracks.length > 0) {
            state.separating = false;
            state.separated = false;
            state.availableTracks = TRACKS.filter(track => tracks.includes(track));
            renderStemSelection();
            updateNavigationControls();
            showToast(
                `分离结果不完整，缺少音轨: ${missingTracks.join(', ')}`,
                'error'
            );
            return;
        }

        state.separating = false;
        state.separationTaskId = null;
        document.getElementById('btn-cancel-sep')?.classList.add('hidden');
        state.separated = true;
        state.selectedTracks.clear();
        state.analysisResults = {};
        state.analysisResultPlugins = {};
        state.analyzerSelections = {};
        state.analysisPendingPlugins = {};
        state.availableTracks = TRACKS.filter(track => tracks.includes(track));
        renderStemSelection();
        renderAnalysisConfig();
        updateNavigationControls();
        showToast('分离完成', 'success');
        loadAnalyzerPlugins().then(() => renderAnalysisConfig());
    }

    function onSeparationFailed(payload = {}) {
        if (payload.task_id && state.separationTaskId && payload.task_id !== state.separationTaskId) return;
        state.separating = false;
        state.separationTaskId = null;
        document.getElementById('btn-cancel-sep')?.classList.add('hidden');
        state.separated = false;
        renderStemSelection();
        updateNavigationControls();
        const msg = payload.error || 'unknown error';
        showToast(`分离失败: ${msg}`, 'error');
        for (const suffix of ['', '-2']) {
            const label = document.getElementById(`sep-ring-label${suffix}`);
            if (label) label.textContent = 'failed';
        }
    }

    async function waitForOperation(taskId) {
        while (true) {
            const response = await api.getTask(taskId);
            if (!response.ok) throw new Error(response.error || '无法查询操作状态');
            const task = response.data || response;
            if (task.status === 'done') return;
            if (['failed', 'cancelled', 'interrupted'].includes(task.status)) {
                throw new Error(task.error || task.status);
            }
            await new Promise(resolve => setTimeout(resolve, 500));
        }
    }

    function onSeparationCancelled(payload = {}) {
        if (payload.task_id && state.separationTaskId && payload.task_id !== state.separationTaskId) return;
        state.separating = false;
        state.separationTaskId = null;
        document.getElementById('btn-cancel-sep')?.classList.add('hidden');
        updateNavigationControls();
        showToast('分离已取消', 'info');
    }

    function renderStemSelection() {
        const container = document.getElementById('stem-selection-list');
        const count = document.getElementById('selected-track-count');
        if (count) count.textContent = `${state.selectedTracks.size} / ${TRACKS.length}`;
        if (!container) return;

        if (!state.separated) {
            const message = state.separating
                ? '正在分离音轨...'
                : '完成分离后可选择音轨';
            container.innerHTML = `<p class="empty-msg compact">${message}</p>`;
            return;
        }

        container.innerHTML = TRACKS.map(track => {
            const available = state.availableTracks.includes(track);
            const selected = state.selectedTracks.has(track);
            return `
                <button type="button"
                    class="stem-row ${selected ? 'selected' : ''}"
                    data-track="${track}"
                    aria-pressed="${selected}"
                    style="--track-color:${TRACK_COLORS[track]}"
                    ${available && !state.selectionSaving ? '' : 'disabled'}>
                    <span class="stem-label" style="color:${TRACK_COLORS[track]}">
                        ${TRACK_LABELS[track]}
                    </span>
                    <span class="stem-select-indicator" aria-hidden="true"></span>
                </button>`;
        }).join('');

        container.querySelectorAll('.stem-row').forEach(row => {
            row.addEventListener('click', () => toggleTrackSelection(row.dataset.track));
        });
    }

    async function toggleTrackSelection(track) {
        if (
            !state.currentWid
            || !state.separated
            || state.selectionSaving
            || !state.availableTracks.includes(track)
        ) {
            return;
        }

        const wid = state.currentWid;
        const previous = new Set(state.selectedTracks);
        if (state.selectedTracks.has(track)) {
            state.selectedTracks.delete(track);
        } else {
            state.selectedTracks.add(track);
        }
        state.selectionSaving = true;
        renderStemSelection();
        renderAnalysisConfig();
        updateNavigationControls();

        const response = await api.updateSelectedTracks(
            wid,
            TRACKS.filter(name => state.selectedTracks.has(name))
        );
        if (state.currentWid !== wid) return;
        state.selectionSaving = false;
        if (!response.ok) {
            state.selectedTracks = previous;
            showToast(`保存音轨选择失败: ${response.error}`, 'error');
        }
        renderStemSelection();
        renderAnalysisConfig();
        updateNavigationControls();
    }
    return { bindStep2, updateDlProgress, updateSepProgress, onSeparationDone, onSeparationFailed, waitForOperation, onSeparationCancelled, renderStemSelection };
}
