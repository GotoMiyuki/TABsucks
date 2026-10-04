import { createSeparationController } from './separation_controller.js?v=20260926p2';
import { createPlaybackController } from './playback_controller.js?v=20260926p2';
import { createAnalysisController } from './analysis_controller.js?v=20260926p2';
/**
 * TABsucks 主控制器
 * 工作流：欢迎页 ↔ active 车间（输入 → 分离/选轨 → 分析 → 播放导出）
 *
 * 重要契约（与 src/kernel/core/workshop.py 一致）：
 * - 任何点击"切换 / 关闭"前必须先 disable 车间所有控件（setBusy(true)）
 * - 关闭 = deactivate（列表里仍在，可再点）
 * - 删除 = 永久（不可恢复，除非 keep_state=true）
 */

import api from './api.js?v=20260926p2';
import EventStream from './event_stream.js?v=20260926p2';

import { state, TRACKS, TRACK_LABELS, TRACK_COLORS } from './app_state.js?v=20260926p2';

const stream = new EventStream();
let _tabSaveChain = Promise.resolve();

const { onAnalysisStarted, onAnalysisProgress, onAnalysisDone, onAnalysisFailed, onAnalysisCancelled, loadAnalyzerPlugins, initializeAnalyzerSelections, isTrackAnalysisComplete, renderAnalysisConfig, cancelAnalysisBatch, handleRunAllAnalyses } = createAnalysisController({ showToast, updateNavigationControls });
const { bindPlayback, bindSpeedCycle, loadTab4, syncTab4ZoomControls, destroyTab4Playback, pausePlayback } = createPlaybackController({ showToast, requestTab, allSelectedTracksAnalyzed });


const { bindStep2, updateDlProgress, updateSepProgress, onSeparationDone, onSeparationFailed, waitForOperation, onSeparationCancelled, renderStemSelection } = createSeparationController({ showToast, updateNavigationControls, cancelAnalysisBatch, handleRunAllAnalyses, renderAnalysisConfig, loadAnalyzerPlugins });

// ══════════════════════════════════════
//  Init
// ══════════════════════════════════════

function bindNavigation() {
    // Tab 指示器点击
    document.querySelectorAll('.step-indicator').forEach(el => {
        el.addEventListener('click', () => {
            const tab = parseInt(el.dataset.tab, 10);
            if (tab >= 1 && tab <= 4) requestTab(tab);
        });
    });
}

document.addEventListener('DOMContentLoaded', async () => {
    bindNavigation();
    bindStep1();
    bindStep2();
    bindPlayback();
    bindSpeedCycle();

    stream
        .on('separation_progress', p => updateSepProgress(p.progress))
        .on('separation_done', p => onSeparationDone(p))
        .on('separation_failed', p => onSeparationFailed(p))
        .on('separation_cancelled', p => onSeparationCancelled(p))
        .on('analysis_started', p => onAnalysisStarted(p))
        .on('analysis_progress', p => onAnalysisProgress(p.track, p.progress, p.task_id))
        .on('analysis_done', p => onAnalysisDone(p))
        .on('analysis_failed', p => onAnalysisFailed(p))
        .on('analysis_cancelled', p => onAnalysisCancelled(p))
        .on('url_download_progress', p => updateDlProgress(p.progress));

    // 启动时建立 SSE（一次连接永久用，按 wid 过滤）
    try {
        stream.connect();
    } catch (e) {
        console.warn('[app] SSE connect failed:', e);
    }

    await refreshWorkshopList();
});

// ══════════════════════════════════════
//  Workshop sidebar
// ══════════════════════════════════════

async function refreshWorkshopList() {
    const r = await api.listWorkshops();
    if (!r.ok) return;
    // 后端 list 直接返回 list，不是 {data: list}
    const list = Array.isArray(r) ? r : (r.data || []);
    state.workshops = list;
    renderWorkshopList();
}

function renderWorkshopList() {
    const root = document.getElementById('workshop-list');
    root.innerHTML = '';
    for (const w of state.workshops) {
        const item = document.createElement('div');
        item.className = 'workshop-item' + (w.active ? ' active' : '');
        item.dataset.wid = w.id;

        const nameSpan = document.createElement('span');
        nameSpan.className = 'workshop-name';
        nameSpan.textContent = w.name;
        nameSpan.title = w.name;
        item.appendChild(nameSpan);

        // close × 按钮（hover 才清晰可见）
        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'workshop-close';
        closeBtn.title = '关闭（不删除，可再激活）';
        closeBtn.textContent = '×';
        closeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            handleCloseWorkshop(w.id);
        });
        item.appendChild(closeBtn);

        // 主点击 → 切换
        item.addEventListener('click', () => handleSwitchWorkshop(w.id));

        root.appendChild(item);
    }
    renderWelcomePanel();
}

function renderWelcomePanel() {
    const panel = document.getElementById('welcome-panel');
    const mainPanels = document.querySelectorAll('.step-panel, #bottom-bar');
    if (state.currentWid === null) {
        // 欢迎页：显示欢迎面板，隐藏所有 step 内容
        panel.classList.remove('hidden');
        mainPanels.forEach(p => p.classList.add('hidden'));
        document.getElementById('btn-delete-active').classList.add('hidden');
    } else {
        panel.classList.add('hidden');
        mainPanels.forEach(p => p.classList.remove('hidden'));
        document.getElementById('btn-delete-active').classList.remove('hidden');
    }
}

async function handleNewWorkshop() {
    if (state.busy) return;
    state.busy = true;
    setBusyOverlay(true);
    try {
        const r = await api.createWorkshop('New Workshop');
        if (!r.ok) {
            showToast(`新建失败: ${r.error || '未知错误'}`, 'error');
            return;
        }
        // POST /workshops 已在后端把新车间设为 active。
        const info = r.data || r;
        const newWid = info.id;
        if (!newWid) {
            showToast('新建失败: 未返回 id', 'error');
            return;
        }
        await activateWorkshopUi(newWid, { switchServer: false });
    } catch (error) {
        console.error('[WORKSHOP-CREATE] activation failed', error);
        showToast(`新建车间失败: ${error?.message || error}`, 'error');
    } finally {
        state.busy = false;
        setBusyOverlay(false);
    }
}

async function handleSwitchWorkshop(wid) {
    if (state.busy) return;
    if (state.currentWid === wid) return;
    state.busy = true;
    // UI 立刻 disable 所有写控件（按你最新定义）
    setControlsDisabled(true);
    document.body.classList.add('busy');
    try {
        await activateWorkshopUi(wid);
    } catch (error) {
        console.error('[WORKSHOP-SWITCH] restore failed', error);
        showToast(`加载车间失败: ${error?.message || error}`, 'error');
    } finally {
        state.busy = false;
        document.body.classList.remove('busy');
        setControlsDisabled(false);
    }
}

async function activateWorkshopUi(wid, { switchServer = true } = {}) {
    if (switchServer) {
        const r = await api.switchWorkshop(wid);
        if (!r.ok) {
            throw new Error(r.error || '切换失败');
        }
    }
    state.currentWid = wid;
    stream.setWorkshopId(wid);
    await refreshWorkshopList();
    renderWelcomePanel();
    await loadActiveWorkshopData();
}

async function handleCloseWorkshop(wid) {
    if (state.busy) return;
    if (state.currentWid === wid) {
        // 关闭当前 active → UI 立即 disable（你最新约定）
        state.busy = true;
        setControlsDisabled(true);
        document.body.classList.add('busy');
    }
    const r = await api.closeWorkshop(wid);
    if (!r.ok) {
        state.busy = false;
        setControlsDisabled(false);
        document.body.classList.remove('busy');
        showToast(`关闭失败: ${r.error}`, 'error');
        return;
    }
    if (r.operation_id) {
        showToast('正在等待当前任务安全结束后关闭车间', 'info');
        try {
            await waitForOperation(r.operation_id);
        } catch (error) {
            state.busy = false;
            setControlsDisabled(false);
            document.body.classList.remove('busy');
            showToast(`关闭失败: ${error.message}`, 'error');
            return;
        }
    }
    if (state.currentWid === wid) {
        state.currentWid = null;
        stream.setWorkshopId(null);
    }
    await refreshWorkshopList();
    renderWelcomePanel();    // 确保 mainPanels 重新隐藏
    state.busy = false;
    setControlsDisabled(false);
    document.body.classList.remove('busy');
}

async function handleDeleteActive() {
    if (state.busy || !state.currentWid) return;
    const wid = state.currentWid;
    const confirmed = confirm(
        '确定要永久删除这个车间吗？\n相关音频和分析结果都会被删除。\n\n提示：传 keep_state=true 时，state.json 会备份到 recycle_bin/。'
    );
    if (!confirmed) return;
    state.busy = true;
    setControlsDisabled(true);
    document.body.classList.add('busy');
    showToast('正在永久删除车间，请稍候...', 'info');
    try {
        const r = await api.deleteWorkshop(wid, { keepState: true });
        if (!r.ok) {
            showToast(`删除失败: ${r.error}`, 'error');
            return;
        }
        if (r.operation_id) {
            showToast('正在等待当前任务安全结束后删除车间', 'info');
            await waitForOperation(r.operation_id);
        }
        state.currentWid = null;
        stream.setWorkshopId(null);
        await refreshWorkshopList();
        renderWelcomePanel();
        showToast('车间已永久删除', 'success');
    } catch (error) {
        showToast(`删除失败: ${error.message}`, 'error');
    } finally {
        state.busy = false;
        setControlsDisabled(false);
        document.body.classList.remove('busy');
    }
}

function setBusyOverlay(visible) {
    document.body.classList.toggle('overlay-busy', visible);
}

function setControlsDisabled(disabled) {
    // 简单实现：禁用所有 button + input + select（精细控制后续）
    for (const el of document.querySelectorAll('button, input, select, textarea')) {
        if (el.closest('#welcome-panel')) continue;  // 欢迎页按钮不受影响
        el.disabled = !!disabled;
    }
}

// ══════════════════════════════════════
//  加载车间数据
// ══════════════════════════════════════

async function loadActiveWorkshopData() {
    if (!state.currentWid) return;
    const wid = state.currentWid;
    resetWorkshopUiState();
    state.analysisResults = {};
    state.analysisResultPlugins = {};
    state.analyzerSelections = {};
    state.analysisPendingPlugins = {};
    state.analysisRunning.clear();
    cancelAnalysisBatch();
    const r = await api.getWorkshopState(wid);
    if (state.currentWid !== wid) return;
    if (!r.ok) {
        showToast(`加载失败: ${r.error || r}`, 'error');
        return;
    }
    const s = r.data || r;

    // 优先：恢复上次离开时所在的 Tab（LastTab 字段）
    const lastTabMap = { Tab1: 1, Tab2: 2, Tab3: 3, Tab4: 4 };
    const lastStep = lastTabMap[s.LastTab] || 1;
    const tab2 = s.TabState?.Tab2 || {};
    const hasRaw = !!(s.TabState?.Tab1?.RawAudioFilePath);
    const sepDone = s.TabState?.Tab2?.SeparationState === 'done';
    state.hasRawAudio = hasRaw;
    state.separated = sepDone;
    state.availableTracks = TRACKS.filter(
        track => !!tab2.TrackAudioFilePath?.[track]
    );
    state.selectedTracks = new Set(
        (tab2.SelectedTracks || []).filter(
            track => state.availableTracks.includes(track)
        )
    );

    if (sepDone) {
        const persisted = await api.getAnalysisResults(wid);
        if (state.currentWid !== wid) return;
        if (!persisted.ok) {
            showToast(`加载分析结果失败: ${persisted.error}`, 'error');
        }
        const persistedResults = persisted.results
            || persisted.data?.results
            || {};
        const persistedPlugins = persisted.result_plugins
            || persisted.data?.result_plugins
            || {};
        state.analysisResults = { ...persistedResults };
        state.analysisResultPlugins = { ...persistedPlugins };
        await loadAnalyzerPlugins();
        if (state.currentWid !== wid) return;
        initializeAnalyzerSelections();
    }

    let targetTab = lastStep;
    if (!hasRaw && targetTab > 1) targetTab = 1;
    if (!sepDone && targetTab > 2) targetTab = 2;
    if (state.selectedTracks.size === 0 && targetTab > 2) targetTab = 2;
    if (!allSelectedTracksAnalyzed() && targetTab > 3) targetTab = 3;

    const activeTasks = await api.listWorkshopTasks(wid);
    if (activeTasks.ok && state.currentWid === wid) {
        const rows = activeTasks.data || activeTasks;
        for (const task of rows) {
            if (!['queued', 'running', 'cancelling', 'committing'].includes(task.status)) continue;
            if (task.kind === 'separation') {
                state.separating = true;
                state.separationTaskId = task.task_id;
                document.getElementById('btn-cancel-sep')?.classList.remove('hidden');
            } else if (task.kind === 'analysis' && task.track) {
                state.analysisRunning.add(task.track);
                state.analysisTaskIds[task.track] = task.task_id;
            }
        }
    }

    renderStemSelection();
    renderAnalysisConfig();
    setTab(targetTab);
}

function resetWorkshopUiState() {
    state.step = 1;
    state.hasRawAudio = false;
    state.separated = false;
    state.separating = false;
    state.separationTaskId = null;
    state.availableTracks = [];
    state.selectedTracks.clear();
    state.selectionSaving = false;
    state.analysisResults = {};
    state.analysisResultPlugins = {};
    state.analyzerSelections = {};
    state.analysisPendingPlugins = {};
    state.analysisRunning.clear();
    state.analysisTaskIds = {};
    cancelAnalysisBatch();
    destroyTab4Playback();
    state.trackVizData = {};
    state.timelineZoom = 1;
    syncTab4ZoomControls();
    state.playing = false;
    state.currentTime = 0;

    document.getElementById('btn-continue-tab2')?.classList.add('hidden');
    document.getElementById('audio-info')?.classList.add('hidden');
    document.getElementById('sep-ring-wrap-2')?.classList.add('hidden');
    document.getElementById('btn-cancel-sep')?.classList.add('hidden');
    document.getElementById('analysis-config-list')?.replaceChildren();
    renderStemSelection();
    updateSepProgress(0);
    updateNavigationControls();
}

function setTab(n) {
    const previousStep = state.step;
    if (previousStep === 4 && n !== 4) {
        pausePlayback();
    }
    state.step = n;
    document.querySelectorAll('.step-panel').forEach(p => p.classList.remove('active'));
    document.querySelector(`.step-panel#step-${n}`)?.classList.add('active');
    document.querySelectorAll('.step-indicator').forEach((el, i) => {
        el.classList.toggle('active', i < n);
    });
    if (n === 3) renderAnalysisConfig();
    const playback = document.getElementById('playback-controls');
    if (playback) playback.classList.toggle('hidden', n !== 4);
    if (n === 4) void loadTab4();
    updateNavigationControls();
    if (state.currentWid) {
        persistCurrentTab(state.currentWid, `Tab${n}`);
    }
}

function persistCurrentTab(wid, tab) {
    _tabSaveChain = _tabSaveChain.then(async () => {
        if (state.currentWid !== wid) return;
        const response = await api.updateCurrentTab(wid, tab);
        if (!response.ok && state.currentWid === wid) {
            console.warn('[TAB-NAV] failed to persist current tab:', response.error);
        }
    });
}

function requestTab(n) {
    if (n <= state.step) {
        setTab(n);
        return;
    }
    if (n >= 2 && !state.hasRawAudio) {
        showToast('请先在 Tab1 上传音频', 'warning');
        return;
    }
    if (n >= 3 && !state.separated) {
        showToast('请先在 Tab2 完成音轨分离', 'warning');
        return;
    }
    if (n >= 3 && state.selectedTracks.size === 0) {
        showToast('请先在 Tab2 选择至少一条音轨', 'warning');
        return;
    }
    if (n >= 4 && !allSelectedTracksAnalyzed()) {
        showToast('请先完成所有已选音轨的分析', 'warning');
        return;
    }
    setTab(n);
}

function allSelectedTracksAnalyzed() {
    return state.selectedTracks.size > 0
        && [...state.selectedTracks].every(track => isTrackAnalysisComplete(track))
        && state.analysisRunning.size === 0;
}

function updateNavigationControls() {
    document.getElementById('btn-cancel-analysis')?.classList.toggle(
        'hidden', state.analysisRunning.size === 0
    );
    const prev = document.getElementById('btn-prev');
    const next = document.getElementById('btn-next');
    if (prev) prev.classList.toggle('hidden', state.step <= 1);
    if (!next) return;

    next.classList.toggle('hidden', state.step >= 4);
    if (state.step === 1) {
        next.disabled = !state.hasRawAudio;
        next.textContent = 'next';
    } else if (state.step === 2) {
        next.disabled = !state.separated
            || state.selectedTracks.size === 0
            || state.selectionSaving;
        next.textContent = 'next';
    } else if (state.step === 3) {
        next.disabled = !allSelectedTracksAnalyzed();
        next.textContent = 'next';
    }
}

// ══════════════════════════════════════
//  Step 1 — INPUT
// ══════════════════════════════════════

function bindStep1() {
    document.getElementById('btn-new-workshop')?.addEventListener('click', handleNewWorkshop);
    document.getElementById('btn-welcome-new')?.addEventListener('click', handleNewWorkshop);

    const fileInput = document.getElementById('file-input');
    document.getElementById('btn-upload-file')?.addEventListener('click', () => fileInput.click());
    fileInput?.addEventListener('change', handleFileUpload);

    const urlBtn = document.getElementById('btn-upload-url');
    const urlWrap = document.getElementById('input-url-wrap');
    if (urlBtn && urlWrap) {
        urlBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log('[TAB1] toggle URL wrap, was hidden:', urlWrap.classList.contains('hidden'));
            if (urlWrap.classList.contains('hidden')) {
                urlWrap.classList.remove('hidden');
                const input = document.getElementById('input-url');
                if (input) input.focus();
            } else {
                urlWrap.classList.add('hidden');
            }
            console.log('[TAB1] now hidden:', urlWrap.classList.contains('hidden'));
        });
    } else {
        // debug: 暴露缺失 id
        console.warn('[TAB1] bind failed: urlBtn=', !!urlBtn, 'urlWrap=', !!urlWrap);
    }
    // debug: 暴露到 window 供 console 手动调用
    window.__toggleUrlWrap = function () {
        if (!urlWrap) return 'no urlWrap element';
        const wasHidden = urlWrap.classList.contains('hidden');
        urlWrap.classList.toggle('hidden');
        return `was hidden=${wasHidden}, now hidden=${urlWrap.classList.contains('hidden')}`;
    };
    document.getElementById('btn-fetch')?.addEventListener('click', handleUrlFetch);

    document.getElementById('btn-delete-active')?.addEventListener('click', handleDeleteActive);
}

async function handleFileUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    await ensureWorkshopAndRun(async (wid) => {
        const r = await api.uploadAudio(wid, file);
        if (!r.ok) { showToast(`上传失败: ${r.error}`, 'error'); return; }
        showAudioInfo(file.name);
        state.hasRawAudio = true;
        updateNavigationControls();
        showToast(`上传完成`, 'success');
        await refreshWorkshopList();
        // 不自动跳转——等用户点「继续」
        document.getElementById('btn-continue-tab2')?.classList.remove('hidden');
    });
}

async function handleUrlFetch() {
    if (state.busy) return;
    const urlInput = document.getElementById('input-url');
    const url = urlInput?.value?.trim();
    if (!url) { showToast('请粘贴音频 / 视频 URL', 'warning'); return; }
    if (!(url.startsWith('http://') || url.startsWith('https://'))) {
        showToast('URL 必须以 http(s):// 开头', 'error'); return;
    }
    state.busy = true;
    setControlsDisabled(true);
    document.body.classList.add('busy');

    // 1. 准备 active 车间
    let wid = state.currentWid;
    if (!wid) {
        const created = await api.createWorkshop('Loading from URL…');
        if (!created.ok) {
            state.busy = false; setControlsDisabled(false);
            document.body.classList.remove('busy');
            showToast(`新建失败: ${created.error}`, 'error'); return;
        }
        const info = created.data || created;
        wid = info.id;
        await refreshWorkshopList();
        await handleSwitchWorkshop(wid);
    }

    // 2. 显示进度条
    const progWrap = document.getElementById('dl-progress-wrap');
    const progBar = document.getElementById('dl-progress-bar');
    const progText = document.getElementById('dl-progress-text');
    if (progWrap) progWrap.classList.remove('hidden');
    showToast('下载音频中…', 'info');

    // 3. 调后端
    const r = await api.uploadFromUrl(wid, url);
    state.busy = false;
    setControlsDisabled(false);
    document.body.classList.remove('busy');
    if (progWrap) progWrap.classList.add('hidden');

    if (!r.ok) {
        showToast(`URL 上传失败: ${r.error || '未知错误'}`, 'error'); return;
    }
    const info = r.data || r;
    showAudioInfo(info.filename);
    state.hasRawAudio = true;
    updateNavigationControls();
    showToast(`下载完成: ${info.name}`, 'success');
    // 侧边栏刷新（车间名已经从视频标题更新）
    await refreshWorkshopList();
    // 不自动跳转——等待用户点「继续」
    document.getElementById('btn-continue-tab2')?.classList.remove('hidden');
}

async function ensureWorkshopAndRun(fn) {
    let wid = state.currentWid;
    if (!wid) {
        const r = await api.createWorkshop('New Workshop');
        if (!r.ok) { showToast(`新建车间失败: ${r.error}`, 'error'); return; }
        const info = r.data || r;
        wid = info.id;
        if (!wid) {
            showToast(`新建车间失败: 未返回 id`, 'error');
            return;
        }
        await refreshWorkshopList();
        await handleSwitchWorkshop(wid);
    }
    await fn(wid);
}

function showAudioInfo(name) {
    document.getElementById('audio-info')?.classList.remove('hidden');
    document.getElementById('info-filename').textContent = name;
}

function showToast(msg, kind = 'info') {
    // 极简实现：用一个浮层 div
    let toast = document.getElementById('app-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'app-toast';
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.className = `toast ${kind}`;
    toast.textContent = msg;
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
}
