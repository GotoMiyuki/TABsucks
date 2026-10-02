/** Separation task progress and stem selection. */
import api from './api.js?v=20261002v1';
import { state, TRACKS, TRACK_LABELS, TRACK_COLORS } from './app_state.js?v=20261002v1';
import {escapeHTML} from './ui.js?v=20261002v1';

const SEPARATION_STAGES = {
    queued: '等待开始', loading_audio: '读取原曲', loading_plugin: '准备分离工具',
    waiting_for_gpu: '等待 GPU', preparing_vram: '准备 GPU 资源',
    running_plugin: '分离任务运行中', preparing_model: '准备模型',
    downloading_model: '下载模型文件', loading_model: '加载模型',
    preparing_audio: '准备音频', separating_audio: '分离音轨',
    writing_stems: '生成分轨', reading_stems: '校验分轨',
    committing: '保存结果', saving_results: '保存结果',
    done: '分离完成', failed: '分离失败', cancelled: '分离已取消',
    interrupted: '分离已中断', cancelling: '正在取消，等待模型退出',
};

export function formatSeparationProgress(payload = {}) {
    const data = typeof payload === 'number' ? {progress: payload} : payload;
    const stage = data.stage || 'running_plugin';
    let text = SEPARATION_STAGES[stage] || '分离任务运行中';
    let fraction = null;
    const completed = data.completed;
    const total = data.total;
    if (!['failed','cancelled','cancelling','interrupted'].includes(stage) && Number.isFinite(completed) && completed >= 0) {
        const measured = Number.isFinite(total) && total > 0;
        if (measured) fraction = Math.min(completed / total, 1);
        if (data.unit === 'chunks') {
            text += measured ? ` · ${completed}/${total} 片段（${Math.round(fraction*100)}%）` : ` · 已处理 ${completed} 个片段`;
        } else if (data.unit === 'bytes') {
            const mb = value => `${(value / 1048576).toFixed(1)} MB`;
            text += measured ? ` · ${mb(completed)} / ${mb(total)}` : ` · 已下载 ${mb(completed)}`;
        } else if (data.unit === 'stems') {
            text += measured ? ` · ${completed}/${total} 音轨` : ` · 已处理 ${completed} 条音轨`;
        }
    } else if (Number.isFinite(data.progress) && !['failed','cancelled','cancelling','interrupted'].includes(stage)) {
        fraction = Math.max(0, Math.min(data.progress, 1));
        if (stage !== 'done') text += ` · ${Math.round(fraction*100)}%`;
    }
    if (stage === 'done') fraction = 1;
    if (data.device) text += data.device === 'gpu' ? ' · GPU' : ' · CPU';
    if (data.error) text += `：${data.error}`;
    return {text, fraction};
}

export function createSeparationController({ showToast, updateNavigationControls, cancelAnalysisBatch, handleRunAllAnalyses, renderAnalysisConfig, loadAnalyzerPlugins, confirmAction, onInvalidate, onRefresh }) {
    let renderedDevice = null;
    function bindStep2() {
        // 下拉填充模型列表（进入 Tab2 时拉一次）
        const sel = document.getElementById('sel-separator');
        if (sel) {
            (async () => {
                const r = await api.listSeparatorPlugins();
                if (!r.ok) { sel.innerHTML = '<option value="">— 加载失败 —</option>'; return; }
                const list = r.data || r;
                sel.innerHTML = list.map(p =>
                    `<option value="${escapeHTML(p.name)}">${escapeHTML(p.display_name || p.name)}</option>`
                ).join('') || '<option value="">— 无可用模型 —</option>';
            })();
        }

        document.getElementById('btn-start-sep')?.addEventListener('click', triggerSeparation);
        document.getElementById('btn-cancel-sep')?.addEventListener('click', async () => {
            if (!state.separationTaskId) return;
            const result = await api.cancelTask(state.separationTaskId);
            if (result.ok) updateSepProgress({stage: 'cancelling', task_id: state.separationTaskId});
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
        if (!state.currentWid || state.busy || state.separating || state.analysisRunning.size || !state.hasRawAudio) {
            showToast('请先导入音频并等待当前任务结束', 'warning');
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
        if (state.separated && !(await confirmAction('重新分离音轨', '<p>成功后将替换音轨，并清空分析选择、分析结果及混音设置。</p>', '重新分离'))) return;
        if (wid !== state.currentWid || state.separating) return;
        const previous = {
            separationProgress: state.separationProgress,
            separated: state.separated,
            availableTracks: [...state.availableTracks],
            selectedTracks: new Set(state.selectedTracks),
            analysisResults: { ...state.analysisResults },
            analysisResultPlugins: { ...state.analysisResultPlugins },
            analyzerSelections: { ...state.analyzerSelections },
            analysisPendingPlugins: { ...state.analysisPendingPlugins },
        };
        state.separating = true;
        state.separationTaskId = null;
        state.separationProgress = {stage: 'loading_audio', device};
        onInvalidate();
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
        // 原曲仍可试听；旧分轨在重跑期间停用。
        await onRefresh();
        if (state.currentWid !== wid) return;
        // 显示进度环
        document.getElementById('sep-ring-wrap-2')?.classList.remove('hidden');
        const r = await api.separate(wid, model, device);
        if (state.currentWid !== wid) return;
        if (r.ok && state.separating) state.separationTaskId = r.task_id || null;
        if (!r.ok) {
            state.separating = false;
            state.separationProgress = previous.separationProgress;
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
            await onRefresh();
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

    function updateSepProgress(payload = {}) {
        const data = typeof payload === 'number' ? {progress: payload} : payload;
        if (data.task_id && state.separationTaskId && data.task_id !== state.separationTaskId) return;
        if (data.updated_at && state.separationProgress?.updated_at > data.updated_at) return;
        state.separationProgress = {...data};
        renderSepProgress();
    }

    function renderSepProgress() {
        const data = state.separationProgress;
        for (const input of document.querySelectorAll('input[name="sep-device"]')) {
            input.disabled = state.busy || state.separating;
            if ((data?.device === 'cpu' || data?.device === 'gpu') && data.device !== renderedDevice) input.checked = input.value === data.device;
        }
        renderedDevice = data?.device || null;
        const status = document.getElementById('sep-status');
        if (status) {
            status.classList.toggle('hidden', !data);
            status.textContent = data ? formatSeparationProgress(data).text : '';
        }
        const {fraction} = formatSeparationProgress(data || {});
        // 同时更新 Tab1 和 Tab2 的进度环（避免 DOM 重复 id 问题）
        for (const suffix of ['', '-2']) {
            const ring = document.getElementById(`sep-ring-fg${suffix}`);
            const label = document.getElementById(`sep-ring-label${suffix}`);
            if (ring) ring.setAttribute('stroke-dashoffset', String(120 - 120 * (fraction || 0)));
            if (label) label.textContent = fraction === null ? '处理中…' : `阶段 ${(fraction * 100).toFixed(0)}%`;
            if (label) label.textContent = !data ? '' : data.stage === 'done' ? '完成' : data.stage === 'failed' ? '失败' : label.textContent;
            document.getElementById(`sep-ring-wrap${suffix}`)?.classList.toggle('hidden', !data || !state.separating);
            if (ring) {
                const wrap = ring.closest('.sep-ring-wrap');
                if (wrap) wrap.classList.toggle('hidden', !data || !state.separating);
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
        if (tracks.length === 0) {
            state.separating = false;
            state.separated = false;
            state.separationProgress = {stage: 'failed', error: '分离结果没有可用音轨', device: state.separationProgress?.device};
            state.availableTracks = TRACKS.filter(track => tracks.includes(track));
            renderStemSelection();
            updateNavigationControls();
            showToast(
                '分离结果没有可用音轨，请检查模型并重试',
                'error'
            );
            return;
        }

        state.separating = false;
        state.separationTaskId = null;
        document.getElementById('btn-cancel-sep')?.classList.add('hidden');
        state.separated = true;
        state.separationProgress = {stage: 'done', progress: 1, device: state.separationProgress?.device};
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
        await loadAnalyzerPlugins();
        if (state.currentWid === wid) renderAnalysisConfig();
    }

    function onSeparationFailed(payload = {}) {
        if (payload.task_id && state.separationTaskId && payload.task_id !== state.separationTaskId) return;
        state.separating = false;
        state.separationTaskId = null;
        document.getElementById('btn-cancel-sep')?.classList.add('hidden');
        state.separated = false;
        state.separationProgress = {stage: 'failed', error: payload.error || '未知错误', device: state.separationProgress?.device};
        renderStemSelection();
        updateNavigationControls();
        const msg = payload.error || 'unknown error';
        showToast(`分离失败: ${msg}`, 'error');
        for (const suffix of ['', '-2']) {
            const label = document.getElementById(`sep-ring-label${suffix}`);
            if (label) label.textContent = '失败';
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
        state.separationProgress = {stage: 'cancelled', device: state.separationProgress?.device};
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
    return { bindStep2, updateDlProgress, updateSepProgress, renderSepProgress, onSeparationDone, onSeparationFailed, waitForOperation, onSeparationCancelled, renderStemSelection };
}
