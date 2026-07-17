/**
 * TABsucks 主控制器
 * 工作流：欢迎页 ↔ active 车间（输入 → 分离/选轨 → 分析 → 播放导出）
 *
 * 重要契约（与 src/kernel/core/workshop.py 一致）：
 * - 任何点击"切换 / 关闭"前必须先 disable 车间所有控件（setBusy(true)）
 * - 关闭 = deactivate（列表里仍在，可再点）
 * - 删除 = 永久（不可恢复，除非 keep_state=true）
 */

import api from './api.js?v=20260716g';
import EventStream from './event_stream.js?v=20260716g';
import { drawWaveform } from './waveform.js?v=20260716g';
import { calculateTimelineLayout, clampTimelineZoom } from './timeline_zoom.js?v=20260716g';

const TRACKS = ['vocals', 'drums', 'bass', 'piano', 'guitar', 'other'];
const TRACK_LABELS = {
    vocals: 'VOCAL', drums: 'DRUM', bass: 'BASS',
    piano: 'KEYBOARD', guitar: 'GUITAR', other: 'ELSE',
};
const TRACK_COLORS = {
    vocals: '#5b65ff', drums: '#ff9500', bass: '#34c759',
    piano: '#af52de', guitar: '#ff2d55', other: '#8e8e93',
};

const state = {
    workshops: [],          // list {id, name, last_tab, active}
    currentWid: null,       // 当前 active 车间
    step: 1,
    hasRawAudio: false,
    separated: false,
    separating: false,
    availableTracks: [],
    selectedTracks: new Set(),
    selectionSaving: false,
    analysisResults: {},
    analysisResultPlugins: {},
    analyzerSelections: {},
    analysisPendingPlugins: {},
    analysisRunning: new Set(),
    analysisBatchQueue: [],
    analysisBatchCurrent: null,
    analysisBatchRunning: false,
    trackVizData: {},
    audioElements: new Map(),
    tab4LoadToken: 0,
    timelineZoom: 1,
    busy: false,            // 任何"切/关/删/新建"进行中
    // playback
    playing: false,
    currentTime: 0,
    duration: 30,
    speed: 1,
    raf: null,
    lastTs: 0,
};

const stream = new EventStream();
let _tabSaveChain = Promise.resolve();

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
        .on('analysis_started', p => onAnalysisStarted(p))
        .on('analysis_progress', p => onAnalysisProgress(p.track, p.progress))
        .on('analysis_done', p => onAnalysisDone(p))
        .on('analysis_failed', p => onAnalysisFailed(p))
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
        state.currentWid = null;
        stream.setWorkshopId(null);
        await refreshWorkshopList();
        renderWelcomePanel();
        showToast('车间已永久删除', 'success');
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

    renderStemSelection();
    renderAnalysisConfig();
    setTab(targetTab);
}

function resetWorkshopUiState() {
    state.step = 1;
    state.hasRawAudio = false;
    state.separated = false;
    state.separating = false;
    state.availableTracks = [];
    state.selectedTracks.clear();
    state.selectionSaving = false;
    state.analysisResults = {};
    state.analysisResultPlugins = {};
    state.analyzerSelections = {};
    state.analysisPendingPlugins = {};
    state.analysisRunning.clear();
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

// ══════════════════════════════════════
//  Step 2 — SELECT
// ══════════════════════════════════════

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
    if (state.currentWid !== wid) return;
    if (!r.ok) {
        state.separating = false;
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
    state.separating = false;
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

function onAnalysisStarted(payload = {}) {
    const track = payload.track;
    if (!track) return;
    if (payload.plugin) {
        state.analysisPendingPlugins[track] = payload.plugin;
    }
    state.analysisRunning.add(track);
    updateAnalysisCardState(track, 'running');
    updateAnalysisCompletionCount();
    updateNavigationControls();
    showToast(`分析 ${track} 已开始...`, 'info');
}

function onAnalysisProgress(track, progress) {
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
    state.analysisRunning.delete(track);
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
    state.analysisRunning.delete(track);
    delete state.analysisPendingPlugins[track];
    updateAnalysisCardState(track, 'idle');
    updateAnalysisCompletionCount();
    updateNavigationControls();
    showToast(`分析 ${track} 失败: ${payload.error || '未知错误'}`, 'error');
    advanceAnalysisBatch(track);
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

function bindPlayback() {
    document.getElementById('btn-play')?.addEventListener('click', togglePlay);
    document.getElementById('seek-bar')?.addEventListener('input', onSeek);
    document.getElementById('btn-prev')?.addEventListener('click', () => requestTab(Math.max(1, state.step - 1)));
    document.getElementById('btn-next')?.addEventListener('click', () => requestTab(Math.min(4, state.step + 1)));
    document.getElementById('btn-continue-tab2')?.addEventListener('click', () => requestTab(2));
    document.getElementById('tab4-zoom')?.addEventListener('input', event => {
        setTab4Zoom(event.target.value);
    });
    document.getElementById('btn-zoom-out')?.addEventListener('click', () => {
        setTab4Zoom(state.timelineZoom - 0.5);
    });
    document.getElementById('btn-zoom-in')?.addEventListener('click', () => {
        setTab4Zoom(state.timelineZoom + 0.5);
    });
    document.getElementById('tab4-zoom-label')?.addEventListener('click', () => {
        setTab4Zoom(1);
    });
    document.getElementById('btn-export-midi')?.addEventListener(
        'click',
        exportSelectedTracksMidi
    );
    window.addEventListener('resize', () => {
        if (state.step === 4) applyTab4Zoom();
    });
}

function bindSpeedCycle() {
    const speeds = [0.5, 1, 1.5, 2];
    const label = document.getElementById('speed-label');
    document.addEventListener('keydown', (e) => {
        const tag = e.target?.tagName;
        if (
            e.key === ' '
            && state.step === 4
            && !['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(tag)
        ) {
            void togglePlay();
            e.preventDefault();
        }
    });
    label?.addEventListener('click', () => {
        const currentIndex = speeds.indexOf(state.speed);
        state.speed = speeds[(currentIndex + 1) % speeds.length];
        for (const audio of state.audioElements.values()) {
            audio.playbackRate = state.speed;
        }
        label.textContent = `x${state.speed}`;
    });
}

async function loadTab4() {
    const wid = state.currentWid;
    const tracks = TRACKS.filter(track => state.selectedTracks.has(track));
    const container = document.getElementById('tab4-track-list');
    const count = document.getElementById('tab4-track-count');
    if (!container || !wid) return;

    destroyTab4Playback();
    const loadToken = state.tab4LoadToken;
    state.trackVizData = {};
    state.currentTime = 0;
    state.duration = 0;
    if (count) count.textContent = `${tracks.length} tracks`;
    if (tracks.length === 0) {
        container.innerHTML = '<p class="empty-msg">Tab2 尚未选择音轨</p>';
        updatePlaybackUi();
        return;
    }

    container.innerHTML = '<p class="empty-msg">正在加载音轨时间轴...</p>';
    const responses = await Promise.all(
        tracks.map(async track => ({
            track,
            response: await api.getVisualization(wid, track),
        }))
    );
    if (
        state.currentWid !== wid
        || state.step !== 4
        || state.tab4LoadToken !== loadToken
    ) {
        return;
    }

    const errors = {};
    for (const { track, response } of responses) {
        if (!response.ok) {
            errors[track] = response.error || '加载失败';
            continue;
        }
        const data = response.data || response;
        state.trackVizData[track] = data;
        state.duration = Math.max(
            state.duration,
            Number(data.metadata?.duration || data.waveform?.duration || 0)
        );
    }

    renderTab4Tracks(tracks, errors);
    createTab4AudioElements(wid, tracks);
    updatePlaybackBounds();
    updatePlaybackUi();
    updateMidiExportButton();
}

function renderTab4Tracks(tracks, errors = {}) {
    const container = document.getElementById('tab4-track-list');
    if (!container) return;
    container.replaceChildren();

    for (const track of tracks) {
        if (errors[track]) {
            const errorRow = document.createElement('div');
            errorRow.className = 'tab4-track-error';
            errorRow.textContent = `${TRACK_LABELS[track]}: ${errors[track]}`;
            container.appendChild(errorRow);
            continue;
        }

        const data = state.trackVizData[track];
        const row = document.createElement('div');
        row.className = 'tab4-track-row';
        row.dataset.track = track;
        row.style.setProperty('--track-color', TRACK_COLORS[track]);

        const label = document.createElement('div');
        label.className = 'tab4-track-label';
        label.textContent = TRACK_LABELS[track] || track;

        const content = document.createElement('div');
        content.className = 'tab4-time-content';
        content.addEventListener('click', event => {
            const rect = content.getBoundingClientRect();
            if (rect.width <= 0) return;
            const proportion = Math.max(
                0,
                Math.min(1, (event.clientX - rect.left) / rect.width)
            );
            setPlaybackTime(proportion * state.duration);
        });

        const waveformLayer = document.createElement('div');
        waveformLayer.className = 'tab4-waveform-layer';
        const canvas = document.createElement('canvas');
        canvas.dataset.track = track;
        waveformLayer.appendChild(canvas);

        const chordLayer = document.createElement('div');
        chordLayer.className = 'tab4-chord-layer';
        renderChordBlocks(
            chordLayer,
            data?.chords || [],
            state.duration
        );

        const playhead = document.createElement('div');
        playhead.className = 'tab4-playhead';

        content.append(waveformLayer, chordLayer, playhead);
        row.append(label, content);
        container.appendChild(row);
    }
    applyTab4Zoom();
}

function renderChordBlocks(layer, chords, duration) {
    layer.replaceChildren();
    const validChords = Array.isArray(chords)
        ? chords.filter(chord =>
            Number.isFinite(Number(chord.start))
            && Number.isFinite(Number(chord.end))
            && Number(chord.end) > Number(chord.start)
        )
        : [];
    if (validChords.length === 0 || duration <= 0) {
        const empty = document.createElement('div');
        empty.className = 'tab4-chord-empty';
        empty.textContent = 'no chord data';
        layer.appendChild(empty);
        return;
    }

    for (const chord of validChords) {
        const start = Math.max(0, Number(chord.start));
        const end = Math.min(duration, Number(chord.end));
        if (end <= start) continue;
        const block = document.createElement('div');
        block.className = 'tab4-chord-block';
        block.style.left = `${(start / duration) * 100}%`;
        block.style.width = `${((end - start) / duration) * 100}%`;
        block.textContent = chord.name || chord.chord || '?';
        block.title = `${block.textContent}  ${formatTime(start)} - ${formatTime(end)}`;
        layer.appendChild(block);
    }
}

function renderTab4Waveforms() {
    document.querySelectorAll('#tab4-track-list canvas[data-track]').forEach(
        canvas => {
            const track = canvas.dataset.track;
            const peaks = state.trackVizData[track]?.waveform?.peaks || [];
            drawWaveform(canvas, peaks, {
                color: TRACK_COLORS[track] || '#5b65ff',
                bgColor: '#050505',
            });
        }
    );
    updateTab4Playheads();
}

function setTab4Zoom(value) {
    state.timelineZoom = clampTimelineZoom(value);
    syncTab4ZoomControls();
    applyTab4Zoom();
}

function syncTab4ZoomControls() {
    const slider = document.getElementById('tab4-zoom');
    const label = document.getElementById('tab4-zoom-label');
    if (slider) slider.value = String(state.timelineZoom);
    if (label) label.textContent = `${state.timelineZoom}x`;
}

function applyTab4Zoom() {
    const container = document.getElementById('tab4-track-list');
    const firstLabel = container?.querySelector('.tab4-track-label');
    if (!container || !firstLabel) return;
    const labelWidth = firstLabel.getBoundingClientRect().width || 96;
    const layout = calculateTimelineLayout({
        viewportWidth: container.clientWidth,
        labelWidth,
        zoom: state.timelineZoom,
        currentTime: state.currentTime,
        duration: state.duration,
    });
    container.style.setProperty(
        '--tab4-content-width',
        `${layout.contentWidth}px`
    );
    renderTab4Waveforms();
    requestAnimationFrame(() => {
        container.scrollLeft = layout.scrollLeft;
    });
}

async function exportSelectedTracksMidi() {
    const tracks = TRACKS.filter(track => state.selectedTracks.has(track));
    if (!state.currentWid || tracks.length === 0) {
        showToast('没有可导出的已选音轨', 'warning');
        return;
    }
    const button = document.getElementById('btn-export-midi');
    if (button) {
        button.disabled = true;
        button.textContent = 'exporting...';
    }
    const response = await api.exportMidi(state.currentWid, tracks);
    if (!response.ok) {
        showToast(`MIDI 导出失败: ${response.error}`, 'error');
        updateMidiExportButton();
        return;
    }

    const url = URL.createObjectURL(response.blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = response.filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    showToast('MIDI 已导出', 'success');
    updateMidiExportButton();
}

function updateMidiExportButton() {
    const button = document.getElementById('btn-export-midi');
    if (!button) return;
    button.textContent = 'export MIDI';
    button.disabled = state.selectedTracks.size === 0
        || !allSelectedTracksAnalyzed();
}

function createTab4AudioElements(wid, tracks) {
    for (const track of tracks) {
        const audio = document.createElement('audio');
        audio.preload = 'auto';
        audio.src = api.getAudioURL(wid, track);
        audio.playbackRate = state.speed;
        audio.addEventListener('loadedmetadata', () => {
            if (Number.isFinite(audio.duration)) {
                state.duration = Math.max(state.duration, audio.duration);
                updatePlaybackBounds();
                updatePlaybackUi();
            }
        });
        audio.addEventListener('ended', () => {
            const allEnded = [...state.audioElements.values()].every(
                item => item.ended || item.paused
            );
            if (allEnded) {
                state.currentTime = state.duration;
                pausePlayback();
                updatePlaybackUi();
            }
        });
        audio.load();
        state.audioElements.set(track, audio);
    }
}

function destroyTab4Playback() {
    state.tab4LoadToken += 1;
    pausePlayback();
    for (const audio of state.audioElements.values()) {
        audio.removeAttribute('src');
        audio.load();
    }
    state.audioElements.clear();
    state.currentTime = 0;
}

async function togglePlay() {
    if (!state.currentWid || state.step !== 4) return;
    if (state.playing) {
        pausePlayback();
        return;
    }
    const audios = [...state.audioElements.values()];
    if (audios.length === 0) {
        showToast('没有可播放的已选音轨', 'warning');
        return;
    }
    if (state.currentTime >= state.duration - 0.02) {
        setPlaybackTime(0);
    } else {
        synchronizeAudioTime(state.currentTime);
    }

    const results = await Promise.allSettled(
        audios.map(audio => {
            audio.playbackRate = state.speed;
            return audio.play();
        })
    );
    if (!results.some(result => result.status === 'fulfilled')) {
        showToast('音轨播放失败，请检查音频文件', 'error');
        return;
    }
    state.playing = true;
    state.lastTs = performance.now();
    updatePlayButton();
    state.raf = requestAnimationFrame(tick);
}

function pausePlayback() {
    state.playing = false;
    for (const audio of state.audioElements.values()) audio.pause();
    if (state.raf) cancelAnimationFrame(state.raf);
    state.raf = null;
    updatePlayButton();
}

function onSeek(event) {
    if (!state.currentWid || state.step !== 4) return;
    setPlaybackTime(Number(event.target.value));
}

function setPlaybackTime(time) {
    state.currentTime = Math.max(
        0,
        Math.min(state.duration || 0, Number(time) || 0)
    );
    synchronizeAudioTime(state.currentTime);
    updatePlaybackUi();
}

function synchronizeAudioTime(time) {
    for (const audio of state.audioElements.values()) {
        try {
            audio.currentTime = Math.min(
                time,
                Number.isFinite(audio.duration) ? audio.duration : time
            );
        } catch (_) {
            // Metadata may still be loading; loadedmetadata will catch up.
        }
    }
}

function updatePlaybackBounds() {
    const seek = document.getElementById('seek-bar');
    if (!seek) return;
    seek.max = String(Math.max(0, state.duration));
}

function updatePlaybackUi() {
    const seek = document.getElementById('seek-bar');
    const display = document.getElementById('time-display');
    if (seek) seek.value = String(state.currentTime);
    if (display) {
        display.textContent =
            `${formatTime(state.currentTime)} / ${formatTime(state.duration)}`;
    }
    updateTab4Playheads();
    updatePlayButton();
}

function updateTab4Playheads() {
    const proportion = state.duration > 0
        ? Math.max(0, Math.min(1, state.currentTime / state.duration))
        : 0;
    document.querySelectorAll('.tab4-playhead').forEach(playhead => {
        playhead.style.left = `${proportion * 100}%`;
    });
}

function updatePlayButton() {
    const button = document.getElementById('btn-play');
    if (!button) return;
    button.classList.toggle('playing', state.playing);
    button.setAttribute('aria-label', state.playing ? '暂停' : '播放');
    button.innerHTML = state.playing
        ? '<svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="2" width="3.5" height="12"/><rect x="9.5" y="2" width="3.5" height="12"/></svg>'
        : '<svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5v11l9-5.5z"/></svg>';
}

function tick(ts) {
    if (!state.playing) return;
    const audios = [...state.audioElements.values()];
    const master = audios.find(audio => !audio.paused && !audio.ended);
    if (master) {
        state.currentTime = master.currentTime;
        for (const audio of audios) {
            if (
                audio !== master
                && !audio.paused
                && Math.abs(audio.currentTime - state.currentTime) > 0.12
            ) {
                audio.currentTime = state.currentTime;
            }
        }
    } else {
        const elapsed = (ts - (state.lastTs || ts)) / 1000 * state.speed;
        state.currentTime = Math.min(state.duration, state.currentTime + elapsed);
    }
    state.lastTs = ts;
    updatePlaybackUi();
    if (state.currentTime >= state.duration) {
        pausePlayback();
        return;
    }
    state.raf = requestAnimationFrame(tick);
}

function formatTime(seconds) {
    const safe = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(safe / 60);
    const secs = Math.floor(safe % 60);
    return `${minutes}:${String(secs).padStart(2, '0')}`;
}
