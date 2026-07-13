/**
 * TABsucks 主控制器
 * 工作流：欢迎页 ↔ active 车间（INPUT → SELECT → OUTPUT）
 *
 * 重要契约（与 src/kernel/core/workshop.py 一致）：
 * - 任何点击"切换 / 关闭"前必须先 disable 车间所有控件（setBusy(true)）
 * - 关闭 = deactivate（列表里仍在，可再点）
 * - 删除 = 永久（不可恢复，除非 keep_state=true）
 */

import api from './api.js';
import EventStream from './event_stream.js';
import { drawWaveform, drawTimeline, drawPlayhead } from './waveform.js';

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
    step: 1,                // 1=INPUT, 2=SELECT, 3=OUTPUT
    phase: 'separate',       // 仅 step=2 时有意义
    separated: false,
    selectedTracks: new Set(),
    analysisResults: {},
    analysisRunning: new Set(),
    trackVizData: {},
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

// ══════════════════════════════════════
//  Init
// ══════════════════════════════════════

function bindNavigation() {
    // Tab 指示器点击
    document.querySelectorAll('.step-indicator').forEach(el => {
        el.addEventListener('click', () => {
            const tab = parseInt(el.dataset.tab, 10);
            if (tab >= 1 && tab <= 4) setTab(tab);
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
        .on('separation_done', () => onSeparationDone())
        .on('analysis_started', p => onAnalysisStarted(p.track))
        .on('analysis_done', p => onAnalysisDone(p))
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
    const r = await api.createWorkshop('New Workshop');
    if (!r.ok) {
        state.busy = false;
        setBusyOverlay(false);
        showToast(`新建失败: ${r.error || '未知错误'}`, 'error');
        return;
    }
    // 后端返回的是 {id, name, last_tab} dict（wrapped to {ok:true, data: dict}）
    const info = r.data || r;
    const newWid = info.id;
    if (!newWid) {
        state.busy = false;
        setBusyOverlay(false);
        showToast(`新建失败: 未返回 id`, 'error');
        return;
    }
    await refreshWorkshopList();
    // 自动激活新车间
    await handleSwitchWorkshop(newWid);
    state.busy = false;
    setBusyOverlay(false);
}

async function handleSwitchWorkshop(wid) {
    if (state.busy) return;
    if (state.currentWid === wid) return;
    state.busy = true;
    // UI 立刻 disable 所有写控件（按你最新定义）
    setControlsDisabled(true);
    document.body.classList.add('busy');
    const r = await api.switchWorkshop(wid);
    if (!r.ok) {
        state.busy = false;
        document.body.classList.remove('busy');
        setControlsDisabled(false);
        showToast(`切换失败: ${r.error}`, 'error');
        return;
    }
    state.currentWid = wid;
    await refreshWorkshopList();
    // refreshWorkshopList 内部 renderWorkshopList → renderWelcomePanel
    // 第二次 render 确保 mainPanels 显示（创建车间后 currentWid 不再 null）
    renderWelcomePanel();
    await loadActiveWorkshopData();
    state.busy = false;
    document.body.classList.remove('busy');
    setControlsDisabled(false);
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
    const r = await api.deleteWorkshop(wid, { keepState: true });
    state.busy = false;
    setControlsDisabled(false);
    document.body.classList.remove('busy');
    if (!r.ok) {
        showToast(`删除失败: ${r.error}`, 'error');
        return;
    }
    state.currentWid = null;
    await refreshWorkshopList();
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
    const r = await api.getWorkshopState(state.currentWid);
    if (!r.ok) {
        showToast(`加载失败: ${r.error || r}`, 'error');
        return;
    }
    const s = r.data || r;

    // 优先：恢复上次离开时所在的 Tab（LastTab 字段）
    const lastTabMap = { Tab1: 1, Tab2: 2, Tab3: 3, Tab4: 4 };
    const lastStep = lastTabMap[s.LastTab] || 1;
    // 用数据推导限制
    const hasRaw = !!(s.TabState?.Tab1?.RawAudioFilePath);
    const sepDone = s.TabState?.Tab2?.SeparationState === 'done';
    const hasAnalysis = Object.values(s.TabState?.Tab3 || {}).some(
        t => t.AnalysisState === 'done'
    );

    let targetTab = lastStep;
    if (!hasRaw && targetTab > 1) targetTab = 1;
    if (!sepDone && targetTab > 2) targetTab = 2;
    if (!hasAnalysis && targetTab > 3) targetTab = 3;

    setTab(targetTab);
    state.separated = targetTab >= 3;
}

function setTab(n) {
    state.step = n;
    document.querySelectorAll('.step-panel').forEach(p => p.classList.remove('active'));
    document.querySelector(`.step-panel#step-${n}`)?.classList.add('active');
    document.querySelectorAll('.step-indicator').forEach((el, i) => {
        el.classList.toggle('active', i < n);
    });
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
}

function triggerSeparation() {
    if (!state.currentWid) {
        showToast('请先创建车间', 'warning');
        return;
    }
    const sel = document.getElementById('sel-separator');
    const model = sel?.value;
    if (!model) {
        showToast('请先在下拉列表中选择分离模型', 'warning');
        return;
    }
    // 显示进度环
    document.getElementById('sep-ring-wrap-2')?.classList.remove('hidden');
    api.separate(state.currentWid, model).then(r => {
        if (!r.ok) { showToast(`启动分离失败: ${r.error}`, 'error'); return; }
        showToast('分离任务已启动，等待结果...', 'info');
    });
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

function onSeparationDone() {
    state.separated = true;
    showToast('分离完成', 'success');
    // 跳到 Step 3（可视化可选展示）
    setTab(3);
}

function onAnalysisStarted(track) {
    state.analysisRunning.add(track);
    showToast(`分析 ${track} 已开始...`, 'info');
}

function onAnalysisDone(payload) {
    const track = payload.track;
    state.analysisRunning.delete(track);
    state.analysisResults[track] = payload.result;
    showToast(`分析 ${track} 完成`, 'success');
}

// ══════════════════════════════════════
//  Step 3 — OUTPUT (visualization)
// ══════════════════════════════════════

function bindPlayback() {
    document.getElementById('btn-play')?.addEventListener('click', togglePlay);
    document.getElementById('seek-bar')?.addEventListener('input', onSeek);
    document.getElementById('btn-prev')?.addEventListener('click', () => setTab(Math.max(1, state.step - 1)));
    document.getElementById('btn-next')?.addEventListener('click', () => setTab(Math.min(4, state.step + 1)));
    document.getElementById('btn-continue-tab2')?.addEventListener('click', () => setTab(2));
}

function bindSpeedCycle() {
    let speeds = [0.5, 1, 1.5, 2];
    let idx = 1;
    const label = document.getElementById('speed-label');
    document.addEventListener('keydown', (e) => {
        if (e.key === ' ' && e.target.tagName !== 'INPUT') { togglePlay(); e.preventDefault(); }
    });
    label?.parentElement?.addEventListener('click', () => {
        idx = (idx + 1) % speeds.length;
        label.textContent = `x${speeds[idx]}`;
    });
}

function togglePlay() {
    if (!state.currentWid) return;
    state.playing = !state.playing;
    updatePlayButton();
    if (state.playing) requestAnimationFrame(tick);
}

function onSeek(e) {
    if (!state.currentWid) return;
    // 占位：未来把 seek 发到 kernel.player.seek()
}

function updatePlayButton() {
    document.getElementById('btn-play')?.classList.toggle('playing', state.playing);
}

function tick(ts) {
    if (!state.playing) return;
    state.currentTime = (state.currentTime + (ts - (state.lastTs || ts)) / 1000 * state.speed) % state.duration;
    state.lastTs = ts;
    drawPlayhead(state.currentTime);
    document.getElementById('time-display').textContent =
        `${Math.floor(state.currentTime / 60)}:${String(Math.floor(state.currentTime % 60)).padStart(2, '0')}`;
    requestAnimationFrame(tick);
}
