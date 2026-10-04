import api from './api.js?v=20261002v1';
import EventStream from './event_stream.js?v=20261002v1';
import {state, TRACKS, TRACK_LABELS} from './app_state.js?v=20261002v1';
import {createSeparationController} from './separation_controller.js?v=20261002v1';
import {createAnalysisController} from './analysis_controller.js?v=20261002v1';
import {createPlaybackController} from './playback_controller.js?v=20261002v1';
import {$, escapeHTML, askDialog, showToast, clearFeedback} from './ui.js?v=20261002v1';

const stream = new EventStream();
let draftImport = false, newProject = false, tabSave = Promise.resolve(), refreshGeneration = 0;
let reconnected = false, reconnecting = false;
const analysis = createAnalysisController({showToast,updateNavigationControls});
const separation = createSeparationController({showToast,updateNavigationControls,cancelAnalysisBatch:analysis.cancelAnalysisBatch,handleRunAllAnalyses:analysis.handleRunAllAnalyses,renderAnalysisConfig:analysis.renderAnalysisConfig,loadAnalyzerPlugins:analysis.loadAnalyzerPlugins,confirmAction:askDialog,onInvalidate:invalidatePlayback,onRefresh:refreshStudio});
const player = createPlaybackController({showToast,requestTab,onSelectTrack:toggleSelection});

document.addEventListener('DOMContentLoaded', async () => {
    player.bindPlayback(); player.bindSpeedCycle(); separation.bindStep2();
    document.querySelectorAll('[data-tab]').forEach(el => el.addEventListener('click', () => requestTab(Number(el.dataset.tab))));
    $('btn-new-workshop').onclick = () => openImport(true);
    $('btn-welcome-new').onclick = () => openImport(true);
    $('btn-close-import').onclick = () => { draftImport = false; newProject = false; state.step = 4; renderLayout(); };
    $('btn-upload-file').onclick = () => $('file-input').click();
    $('file-input').onchange = async e => { const file = e.target.files[0]; if (file) await importAudio(file); e.target.value = ''; };
    $('btn-upload-url').onclick = () => { $('input-url-wrap').classList.toggle('hidden'); if (!$('input-url-wrap').classList.contains('hidden')) $('input-url').focus(); };
    $('input-url-wrap').onsubmit = e => { e.preventDefault(); void importAudio(null,$('input-url').value.trim()); };
    const drop = $('drop-zone');
    drop.ondragover = e => { e.preventDefault(); drop.classList.add('dragging'); };
    drop.ondragleave = () => drop.classList.remove('dragging');
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove('dragging'); const files = e.dataTransfer.files; if (files.length !== 1) showToast('一次请选择一个音频或视频文件', 'warning'); else void importAudio(files[0]); };
    window.addEventListener('dragover', e => e.preventDefault());
    window.addEventListener('drop', e => e.preventDefault());
    $('btn-dismiss-feedback').onclick = clearFeedback;
    $('btn-sidebar').onclick = () => { const open = $('sidebar').classList.toggle('open'); $('btn-sidebar').setAttribute('aria-expanded',String(open)); };
    document.addEventListener('click', e => {
        document.querySelectorAll('.project-menu[open]').forEach(menu => { if (!menu.contains(e.target)) menu.open = false; });
        if (!$('sidebar').contains(e.target) && !$('btn-sidebar').contains(e.target)) closeSidebar();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeSidebar(); document.querySelectorAll('.project-menu[open]').forEach(menu => { menu.open = false; menu.querySelector('summary').focus(); }); } });
    $('btn-select-analyzable').onclick = async () => {
        if (state.busy || state.separating || state.selectionSaving) return;
        const plugins = await analysis.loadAnalyzerPlugins();
        await saveSelection(state.availableTracks.filter(track => plugins.some(p => p.input_stems?.includes(track))));
    };
    stream.on('connection', async payload => {
        $('connection-status').textContent = payload.connected ? '本地服务已连接' : '连接中断 · 正在重连';
        if (payload.connected && reconnected && state.currentWid && !state.busy) await reconcileTasks();
        if (payload.connected) reconnected = true;
    });
    const handlers = {
        separation_progress:p => separation.updateSepProgress(p),
        separation_done:async p => {
            const wid = state.currentWid;
            await separation.onSeparationDone(p);
            if (wid !== state.currentWid || !state.separated) return;
            player.useSeparatedSourceWhenIdle(); await refreshStudio();
            if (wid === state.currentWid) requestTab(4);
        },
        separation_failed:async p => { separation.onSeparationFailed(p); await refreshActive(false); },
        separation_cancelled:async p => { separation.onSeparationCancelled(p); await refreshActive(false); },
        analysis_started:p => analysis.onAnalysisStarted(p),
        analysis_progress:p => analysis.onAnalysisProgress(p.track,p.progress,p.task_id),
        analysis_done:async p => { analysis.onAnalysisDone(p); await refreshStudio(); },
        analysis_failed:async p => { analysis.onAnalysisFailed(p); await refreshStudio(); },
        analysis_cancelled:async p => { analysis.onAnalysisCancelled(p); await refreshStudio(); updateNavigationControls(); },
        url_download_progress:p => separation.updateDlProgress(p.progress),
    };
    for (const [type,handler] of Object.entries(handlers)) stream.on(type,payload => {
        const expected = type.startsWith('analysis_') ? state.analysisTaskIds[payload.track] : type.startsWith('separation_') ? state.separationTaskId : null;
        if (type.endsWith('_progress') && payload.task_id && payload.task_id !== expected) return;
        if (payload.task_id && expected && payload.task_id !== expected) return;
        Promise.resolve(handler(payload)).catch(error => showToast(`状态更新失败：${error.message}`, 'error'));
    });
    stream.connect();
    await refreshWorkshopList(); renderLayout();
    const active = state.workshops.find(p => p.active);
    if (active) await activate(active.id,false);
});

function closeSidebar() { $('sidebar').classList.remove('open'); $('btn-sidebar').setAttribute('aria-expanded','false'); }
function openImport(createNew) { if (state.busy) return; draftImport = true; newProject = createNew; state.step = 1; renderLayout(); closeSidebar(); }

async function refreshWorkshopList() {
    const response = await api.listWorkshops();
    if (!response.ok) { showToast(`项目列表加载失败：${response.error}`, 'error'); return; }
    state.workshops = response.data || [];
    const container = $('workshop-list'); container.replaceChildren();
    if (!state.workshops.length) container.innerHTML = '<p class="help-text" style="padding:12px 8px">还没有项目。导入一首歌曲开始。</p>';
    for (const project of state.workshops) {
        const item = document.createElement('div'); item.className = `workshop-item${project.id === state.currentWid ? ' active' : ''}`;
        item.dataset.projectId = project.id;
        const open = document.createElement('button'); open.className = 'workshop-open'; open.type = 'button'; open.title = project.name;
        open.innerHTML = `<span aria-hidden="true">♫</span><span class="workshop-name">${escapeHTML(project.name)}</span>`;
        open.onclick = () => activate(project.id);
        const menu = document.createElement('details'); menu.className = 'project-menu';
        menu.innerHTML = `<summary aria-label="${escapeHTML(project.name)}的项目操作">⋯</summary><div class="menu-items"><button class="btn-text" data-action="rename" type="button">重命名</button><button class="btn-text" data-action="close" type="button">关闭项目</button><button class="btn-text btn-danger" data-action="delete" type="button">永久删除</button></div>`;
        menu.querySelectorAll('button').forEach(button => { button.onclick = () => { menu.open = false; void projectAction(project,button.dataset.action); }; });
        item.append(open,menu); container.append(item);
    }
    const active = state.workshops.find(p => p.id === state.currentWid);
    $('project-title').textContent = active?.name || '开始一段新的音乐探索';
}

async function activate(wid,switchServer = true) {
    if (state.busy || (state.currentWid === wid && switchServer)) return;
    setBusy(true); player.pausePlayback(); player.saveSession();
    try {
        if (switchServer) {
            const response = await api.switchWorkshop(wid);
            if (!response.ok) throw new Error(response.error || '切换失败');
        }
        player.destroyTab4Playback(); analysis.cancelAnalysisBatch();
        clearFeedback();
        state.currentWid = wid; stream.setWorkshopId(wid); draftImport = false; newProject = false;
        await refreshActive(true); await refreshWorkshopList(); closeSidebar();
    } catch (error) { showToast(`加载项目失败：${error.message}`, 'error'); }
    finally { setBusy(false); renderLayout(); }
}

async function refreshActive(restore) {
    const wid = state.currentWid, token = ++refreshGeneration;
    if (!wid) return;
    const response = await api.getWorkshopState(wid);
    if (wid !== state.currentWid || token !== refreshGeneration) return;
    if (!response.ok) throw new Error(response.error);
    const snapshot = response.data;
    if (restore) state.analysisErrors = {};
    const tab2 = snapshot.TabState?.Tab2 || {};
    state.hasRawAudio = !!snapshot.TabState?.Tab1?.RawAudioFilePath;
    state.separated = tab2.SeparationState === 'done';
    state.availableTracks = TRACKS.filter(track => !!tab2.TrackAudioFilePath?.[track]);
    state.selectionSaving = false;
    state.selectedTracks = new Set((tab2.SelectedTracks || []).filter(track => state.availableTracks.includes(track)));
    state.analysisRunning.clear(); state.analysisTaskIds = {}; state.analysisPendingPlugins = {};
    state.separating = false; state.separationTaskId = null; state.separationProgress = null;
    const tasks = await api.listWorkshopTasks(wid);
    if (wid !== state.currentWid || token !== refreshGeneration) return;
    if (!tasks.ok) showToast(`任务状态加载失败：${tasks.error}`, 'error');
    for (const task of tasks.data || []) {
        if (task.kind === 'analysis' && task.track && ['done','queued','running'].includes(task.status)) delete state.analysisErrors[task.track];
        if (task.kind === 'analysis' && task.track && ['failed','interrupted','cancelled'].includes(task.status)) state.analysisErrors[task.track] = task.error || '任务未完成，可重新分析';
        if (!['queued','running','cancelling','committing'].includes(task.status)) continue;
        if (task.kind === 'separation') {
            state.separating = true; state.separationTaskId = task.task_id;
            separation.updateSepProgress({...task.progress_detail, stage: task.stage, progress: task.progress, task_id: task.task_id, updated_at: task.updated_at});
        }
        if (task.kind === 'analysis' && task.track) { state.analysisRunning.add(task.track); state.analysisTaskIds[task.track] = task.task_id; }
    }
    if (!state.separationProgress && tab2.SeparationState !== 'not_started') {
        const latest = (tasks.data || []).filter(task => task.kind === 'separation').at(-1);
        if (latest) separation.updateSepProgress({...latest.progress_detail, stage: latest.status, progress: latest.progress, error: latest.error, updated_at: latest.updated_at});
    }
    const persisted = await api.getAnalysisResults(wid);
    if (wid !== state.currentWid || token !== refreshGeneration) return;
    if (!persisted.ok) showToast(`分析结果加载失败：${persisted.error}`, 'error');
    state.analysisResults = persisted.results || {}; state.analysisResultPlugins = persisted.result_plugins || {};
    if (restore) state.analyzerSelections = {};
    await analysis.loadAnalyzerPlugins();
    if (wid !== state.currentWid || token !== refreshGeneration) return;
    analysis.initializeAnalyzerSelections();
    if (restore) {
        const mix = Object.fromEntries(Object.entries(snapshot.TabState?.Tab4 || {}).map(([track,value]) => [track,{volume:1,mute:false,solo:false,...value.MixState}]));
        player.restoreSession(wid,mix);
        state.step = Number((snapshot.LastTab || 'Tab1').slice(-1));
        if (!state.hasRawAudio) state.step = 1;
    }
    $('info-filename').textContent = (snapshot.TabState?.Tab1?.RawAudioFilePath || '').split(/[\\/]/).pop().replace(/^[a-f0-9]{32}_/, '') || snapshot.WorkshopName;
    renderLayout(); analysis.renderAnalysisConfig(); await refreshStudio();
}

async function refreshStudio() { if (state.hasRawAudio) await player.loadTab4(); updateNavigationControls(); }
async function reconcileTasks() {
    if (reconnecting || state.busy) return;
    reconnecting = true;
    try {
        analysis.cancelAnalysisBatch();
        const projects = await api.listWorkshops();
        if (!projects.ok) throw new Error(projects.error);
        const active = projects.data.find(project => project.active);
        if (!active) {
            const resumed = await api.switchWorkshop(state.currentWid);
            if (!resumed.ok) throw new Error(resumed.error);
            invalidatePlayback();
            await refreshWorkshopList(); await refreshActive(true);
        } else if (active.id !== state.currentWid) {
            player.pausePlayback(); player.saveSession(); player.destroyTab4Playback();
            state.currentWid = null; state.hasRawAudio = false; stream.setWorkshopId(null);
            await refreshWorkshopList(); renderLayout();
            showToast('项目已在其他窗口切换，请从项目列表重新打开', 'warning');
            return;
        } else await refreshActive(false);
        showToast('连接已恢复，项目与任务状态已重新核对', 'info');
    } catch (error) { showToast(`状态恢复失败：${error.message}`, 'error'); }
    finally { reconnecting = false; }
}

async function projectAction(project,action) {
    if (state.busy) return;
    if (action === 'rename') {
        const data = await askDialog('重命名项目', `<p>为项目起一个容易识别的名字。</p><input type="text" name="name" value="${escapeHTML(project.name)}" required maxlength="200" aria-label="项目名称">`, '保存');
        const name = data?.get('name')?.trim(); if (!name) return;
        const result = await api.renameWorkshop(project.id,name);
        if (!result.ok) showToast(`重命名失败：${result.error}`, 'error');
        else {
            await refreshWorkshopList();
            document.querySelector(`[data-project-id="${CSS.escape(project.id)}"] summary`)?.focus();
        }
        return;
    }
    if (action === 'delete' && !(await askDialog('永久删除项目', `<p>确定删除“${escapeHTML(project.name)}”？音频和分析结果将被永久删除。</p>`, '永久删除'))) return;
    setBusy(true); if (state.currentWid === project.id) player.pausePlayback();
    try {
        const result = action === 'delete' ? await api.deleteWorkshop(project.id) : await api.closeWorkshop(project.id);
        if (!result.ok) throw new Error(result.error);
        if (result.operation_id) { showToast('正在等待当前任务安全退出…'); await separation.waitForOperation(result.operation_id); }
        if (state.currentWid === project.id) {
            player.saveSession(); player.destroyTab4Playback({forget:action === 'delete'}); analysis.cancelAnalysisBatch();
            state.currentWid = null; state.hasRawAudio = false; state.availableTracks = []; state.selectedTracks.clear(); stream.setWorkshopId(null);
        }
        draftImport = false; await refreshWorkshopList(); showToast(action === 'delete' ? '项目已删除' : '项目已关闭，可从列表重新打开', 'success');
    } catch (error) { showToast(`操作失败：${error.message}`, 'error'); }
    finally { setBusy(false); renderLayout(); }
}

function renderLayout() {
    const active = !!state.currentWid;
    $('welcome-panel').classList.toggle('hidden',active || draftImport);
    $('step-bar').classList.toggle('hidden',!active || draftImport);
    for (let i = 1; i <= 3; i++) $(`step-${i}`).classList.toggle('hidden',i === 1 ? !(draftImport || (active && state.step === 1)) : !active || draftImport || state.step !== i);
    $('step-4').classList.toggle('hidden',!active || !state.hasRawAudio || draftImport);
    $('bottom-bar').classList.toggle('hidden',!active || !state.hasRawAudio || draftImport);
    $('audio-info').classList.toggle('hidden',!state.hasRawAudio || newProject);
    updateNavigationControls();
}
function requestTab(n) {
    if (state.busy) return;
    if (n > 1 && !state.hasRawAudio) { showToast('请先导入音频', 'warning'); return; }
    if (n === 3 && (!state.separated || state.separating)) { showToast('请先完成音轨分离', 'warning'); return; }
    draftImport = false; newProject = false; state.step = n; renderLayout(); analysis.renderAnalysisConfig();
    if (state.currentWid) {
        const wid = state.currentWid;
        tabSave = tabSave.then(async () => { if (state.currentWid !== wid) return; const result = await api.updateCurrentTab(wid,`Tab${n}`); if (!result.ok && state.currentWid === wid) showToast(`工作区位置保存失败：${result.error}`, 'warning'); }).catch(error => showToast(error.message,'error'));
    }
}
function updateNavigationControls() {
    separation.renderSepProgress();
    document.querySelectorAll('.step-indicator').forEach(el => {
        const n = Number(el.dataset.tab); el.classList.toggle('active',state.step === n); el.setAttribute('aria-current',state.step === n ? 'page' : 'false');
        el.classList.toggle('done',n === 1 ? state.hasRawAudio && state.step !== 1 : n === 2 ? state.separated && state.step !== 2 : false);
        el.disabled = state.busy;
    });
    $('btn-start-sep').disabled = state.busy || !state.hasRawAudio || state.separating || state.analysisRunning.size > 0;
    $('btn-start-sep').textContent = state.separating ? '正在分离…' : state.separated ? '重新分离' : '开始分离';
    $('btn-cancel-sep').classList.toggle('hidden',!state.separating);
    $('btn-cancel-analysis').classList.toggle('hidden',!state.analysisRunning.size);
    player.updateControls();
}
function setBusy(busy) { state.busy = busy; document.body.classList.toggle('busy',busy); $('btn-new-workshop').disabled = busy; analysis.renderAnalysisConfig(); updateNavigationControls(); }
function invalidatePlayback() { player.destroyTab4Playback({forget:true}); state.mix = {}; state.resultSelections = {}; state.loop = {a:null,b:null,enabled:false}; state.currentTime = 0; state.speed = 1; state.timelineZoom = 1; state.source = 'full'; }

async function importAudio(file,url = '') {
    if (state.busy) return;
    if (file && file.size === 0) { showToast('文件为空，请重新选择', 'warning'); return; }
    if (!file && !/^https?:\/\//i.test(url)) { showToast('请输入 http 或 https 音频/视频链接', 'warning'); return; }
    if (state.currentWid && state.hasRawAudio && !newProject && !(await askDialog('更换音源', '<p>更换后需重新分离和分析，旧混音与练习设置也将重置。</p>', '更换音源'))) return;
    setBusy(true); player.pausePlayback();
    let imported = false;
    try {
        if (!state.currentWid || newProject) {
            player.saveSession();
            const created = await api.createWorkshop('新项目');
            if (!created.ok) throw new Error(created.error);
            player.destroyTab4Playback(); state.currentWid = created.data?.id || created.id; stream.setWorkshopId(state.currentWid); analysis.cancelAnalysisBatch();
            clearFeedback();
        }
        newProject = false; draftImport = false;
        showToast(file ? '正在接收并准备音频…' : '正在获取链接音频…');
        const result = file ? await api.uploadAudio(state.currentWid,file) : await api.uploadFromUrl(state.currentWid,url);
        if (!result.ok) throw new Error(result.error);
        invalidatePlayback(); state.analyzerSelections = {}; state.analysisErrors = {};
        clearFeedback();
        await refreshWorkshopList(); await refreshActive(true); state.step = 2; showToast('音频已导入，可以试听或开始分离', 'success');
        imported = true;
    } catch (error) {
        showToast(`导入失败：${error.message}`, 'error');
        if (state.currentWid) { try { await refreshWorkshopList(); await refreshActive(false); } catch (_) { /* Retain the error and retry entry. */ } }
        state.step = 1;
    } finally { $('dl-progress-wrap').classList.add('hidden'); setBusy(false); renderLayout(); if (imported) requestTab(2); }
}
async function saveSelection(tracks) {
    if (state.busy || state.selectionSaving || !state.separated) return;
    const wid = state.currentWid, previous = state.selectedTracks;
    state.selectedTracks = new Set(tracks); state.selectionSaving = true; updateNavigationControls();
    const response = await api.updateSelectedTracks(wid,tracks);
    if (wid !== state.currentWid) return;
    state.selectionSaving = false;
    if (!response.ok) { state.selectedTracks = previous; showToast(`分析选择保存失败：${response.error}`, 'error'); }
    analysis.initializeAnalyzerSelections(); analysis.renderAnalysisConfig(); updateNavigationControls();
}
async function toggleSelection(track) {
    const selected = new Set(state.selectedTracks);
    selected.has(track) ? selected.delete(track) : selected.add(track);
    await saveSelection(TRACKS.filter(t => selected.has(t)));
}
