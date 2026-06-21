/**
 * TABsucks 主控制器
 * 流程：INPUT → SELECT(separate → analyze) → OUTPUT
 */

import api from './api.js';
import EventStream from './event_stream.js';
import { drawWaveform, drawTimeline, drawPlayhead } from './waveform.js';

const TRACKS = ['vocals', 'drums', 'bass', 'piano', 'guitar', 'other'];
const TRACK_LABELS = { vocals: 'VOCAL', drums: 'DRUM', bass: 'BASS', piano: 'KEYBOARD', guitar: 'GUITAR', other: 'ELSE' };
const TRACK_COLORS = {
    vocals: '#5b65ff', drums: '#ff9500', bass: '#34c759',
    piano: '#af52de', guitar: '#ff2d55', other: '#8e8e93',
};

const state = {
    workshops: [],
    currentWid: null,
    step: 1,            // 1=INPUT, 2=SELECT, 3=OUTPUT
    phase: 'separate',   // 'separate' | 'analyze' (only meaningful when step=2)
    separated: false,
    selectedTracks: new Set(),
    analysisResults: {},
    analysisRunning: new Set(),
    trackVizData: {},
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
        .on('analysis_done', p => onAnalysisDone(p));

    await refreshWorkshopList();
});

// ══════════════════════════════════════
//  Workshop sidebar
// ══════════════════════════════════════

async function refreshWorkshopList() {
    state.workshops = await api.listWorkshops();
    renderWorkshopList();
    if (state.workshops.length > 0 && !state.currentWid) {
        await switchWorkshop(state.workshops[0].id);
    }
}

function renderWorkshopList() {
    const list = document.getElementById('workshop-list');
    list.innerHTML = '';
    for (const ws of state.workshops) {
        const el = document.createElement('div');
        el.className = 'workshop-item' + (ws.id === state.currentWid ? ' active' : '');
        el.innerHTML = `
            <span class="ws-name">${esc(ws.name)}</span>
            <button class="ws-delete" title="delete">&times;</button>
        `;
        el.querySelector('.ws-name').addEventListener('click', () => switchWorkshop(ws.id));
        el.querySelector('.ws-delete').addEventListener('click', e => { e.stopPropagation(); deleteWorkshop(ws.id); });
        list.appendChild(el);
    }
}

async function switchWorkshop(wid) {
    state.currentWid = wid;
    state.separated = false;
    state.selectedTracks.clear();
    state.analysisResults = {};
    state.analysisRunning.clear();
    state.trackVizData = {};
    stopPlayback();
    stream.connect(wid);
    await api.switchWorkshop(wid);
    renderWorkshopList();
    goToStep(1);
}

async function deleteWorkshop(wid) {
    await api.deleteWorkshop(wid);
    if (state.currentWid === wid) { stream.disconnect(); state.currentWid = null; }
    await refreshWorkshopList();
}

document.getElementById('btn-new-workshop').addEventListener('click', async () => {
    const ws = await api.createWorkshop();
    await refreshWorkshopList();
    await switchWorkshop(ws.id);
});

// ══════════════════════════════════════
//  Navigation (step bar + prev/next)
// ══════════════════════════════════════

function bindNavigation() {
    // Step bar click
    document.querySelectorAll('.step-indicator').forEach(el => {
        el.addEventListener('click', () => goToStep(parseInt(el.dataset.step)));
    });
    // Prev / Next
    document.getElementById('btn-prev').addEventListener('click', goPrev);
    document.getElementById('btn-next').addEventListener('click', goNext);
}

function goToStep(n) {
    if (n === 3 && Object.keys(state.analysisResults).length === 0) return;

    state.step = n;
    if (n === 2) {
        state.phase = 'separate';
    }
    if (n === 3) buildOutputTimeline();

    renderStep();
}

function goNext() {
    const { step, phase } = state;
    if (step === 1) {
        goToStep(2);
    } else if (step === 2 && phase === 'separate') {
        if (state.selectedTracks.size === 0) return;
        state.phase = 'analyze';
        buildAnalysisConfig();
        renderStep();
    } else if (step === 2 && phase === 'analyze') {
        goToStep(3);
    }
}

function goPrev() {
    const { step, phase } = state;
    if (step === 2 && phase === 'analyze') {
        state.phase = 'separate';
        renderStep();
    } else if (step === 2 && phase === 'separate') {
        goToStep(1);
    } else if (step === 3) {
        goToStep(2);
    }
}

function renderStep() {
    const { step, phase } = state;

    // Step indicators
    document.querySelectorAll('.step-indicator').forEach(el => {
        el.classList.toggle('active', parseInt(el.dataset.step) === step);
    });

    // Panels
    document.querySelectorAll('.step-panel').forEach(p =>
        p.classList.toggle('active', parseInt(p.id.replace('step-', '')) === step)
    );

    // Step 2 sub-phases
    document.getElementById('phase-separate').classList.toggle('hidden', !(step === 2 && phase === 'separate'));
    document.getElementById('phase-analyze').classList.toggle('hidden', !(step === 2 && phase === 'analyze'));

    // Bottom bar
    const prevBtn = document.getElementById('btn-prev');
    const nextBtn = document.getElementById('btn-next');
    const playback = document.getElementById('playback-controls');

    prevBtn.classList.toggle('hidden', step === 1);
    nextBtn.classList.toggle('hidden', step === 3);
    playback.classList.toggle('hidden', step !== 3);

    // Next button text
    if (step === 2 && phase === 'separate') {
        nextBtn.textContent = 'next';
    } else if (step === 2 && phase === 'analyze') {
        nextBtn.textContent = 'output';
    } else {
        nextBtn.textContent = 'next';
    }

    // Next disabled state
    if (step === 1) {
        nextBtn.style.opacity = state.separated ? '' : '0.4';
        nextBtn.disabled = !state.separated;
    } else if (step === 2 && phase === 'separate') {
        nextBtn.style.opacity = state.selectedTracks.size > 0 ? '' : '0.4';
    } else if (step === 2 && phase === 'analyze') {
        const allDone = [...state.selectedTracks].every(t => state.analysisResults[t]);
        nextBtn.style.opacity = allDone ? '' : '0.4';
    }
}

// ══════════════════════════════════════
//  Step 1: INPUT
// ══════════════════════════════════════

function bindStep1() {
    document.getElementById('btn-upload-file').addEventListener('click', () => {
        document.getElementById('file-input').click();
    });
    document.getElementById('file-input').addEventListener('change', () => {
        const f = document.getElementById('file-input').files[0];
        if (f) handleUpload(f);
    });
    document.getElementById('btn-upload-url').addEventListener('click', () => {
        document.getElementById('input-url-wrap').classList.toggle('hidden');
    });
    document.getElementById('btn-fetch').addEventListener('click', () => {
        const url = document.getElementById('input-url').value.trim();
        if (url) {
            document.getElementById('info-filename').textContent = url;
            document.getElementById('info-duration').textContent = '~';
            document.getElementById('info-samplerate').textContent = '~';
            document.getElementById('audio-info').classList.remove('hidden');
            state.separated = false;
            renderStep();
        }
    });
}

async function handleUpload(file) {
    if (!state.currentWid) return;
    await api.uploadAudio(state.currentWid, file);
    document.getElementById('info-filename').textContent = file.name;
    document.getElementById('info-duration').textContent = 'loading...';
    document.getElementById('info-samplerate').textContent = '~';
    document.getElementById('audio-info').classList.remove('hidden');
    await refreshWorkshopList();

    // Mock info
    setTimeout(() => {
        document.getElementById('info-duration').textContent = '0:30';
        document.getElementById('info-samplerate').textContent = '44100 Hz';
    }, 300);

    // 立即开始分离，显示进度环
    if (!state.separated) {
        startSeparationFromInput();
    }
}

function startSeparationFromInput() {
    const wrap = document.getElementById('sep-ring-wrap');
    const fg = document.getElementById('sep-ring-fg');
    const label = document.getElementById('sep-ring-label');
    wrap.classList.remove('hidden', 'done');
    fg.style.strokeDashoffset = '113.1';
    label.textContent = '0%';
    api.triggerSeparation(state.currentWid);
}

// ══════════════════════════════════════
//  Step 2 — Phase: Separate
// ══════════════════════════════════════

function bindStep2() {
    document.getElementById('btn-model').addEventListener('click', () => {
        // placeholder: model selection popup (future)
    });
    document.getElementById('btn-run-all').addEventListener('click', runAllAnalysis);
}

async function runAllAnalysis() {
    if (!state.currentWid) return;
    const btn = document.getElementById('btn-run-all');
    btn.disabled = true;
    btn.textContent = 'running...';
    for (const track of state.selectedTracks) {
        if (state.analysisRunning.has(track) || state.analysisResults[track]) continue;
        const row = document.querySelector(`.analysis-config-row[data-track="${track}"]`);
        const plugin = row?.querySelector('select')?.value || (track === 'drums' ? 'rhythm_deep' : 'chord_chordnet_2e1d');
        await api.triggerAnalysis(state.currentWid, track, plugin);
    }
}

function buildStemGrid() {
    const grid = document.getElementById('stem-grid');
    grid.innerHTML = '';
    state.selectedTracks = new Set(TRACKS); // default all selected

    for (const track of TRACKS) {
        const sq = document.createElement('div');
        sq.className = 'stem-square selected';
        sq.dataset.track = track;
        sq.innerHTML = `
            <div class="stem-check"></div>
            <span class="stem-label">${TRACK_LABELS[track]}</span>
        `;
        sq.addEventListener('click', () => {
            if (state.selectedTracks.has(track)) {
                state.selectedTracks.delete(track);
                sq.classList.remove('selected');
            } else {
                state.selectedTracks.add(track);
                sq.classList.add('selected');
            }
            renderStep();
        });
        grid.appendChild(sq);
    }
}

function updateSepProgress(progress) {
    const pct = Math.round(progress * 100);
    // INPUT 步骤进度环
    const fg = document.getElementById('sep-ring-fg');
    const label = document.getElementById('sep-ring-label');
    if (fg && label) {
        const circumference = 2 * Math.PI * 18;
        fg.style.strokeDashoffset = circumference * (1 - progress);
        label.textContent = pct + '%';
    }
}

async function onSeparationDone() {
    state.separated = true;
    buildStemGrid();

    // 进度环标记完成
    const wrap = document.getElementById('sep-ring-wrap');
    const label = document.getElementById('sep-ring-label');
    if (wrap) {
        wrap.classList.add('done');
        label.textContent = '✓';
    }

    renderStep(); // next 按钮亮起
}

// Trigger separation when entering step 2
// ══════════════════════════════════════
//  Step 2 — Phase: Analyze
// ══════════════════════════════════════

function buildAnalysisConfig() {
    const list = document.getElementById('analysis-config-list');
    list.innerHTML = '';
    const selected = [...state.selectedTracks];

    for (const track of selected) {
        const isDrums = track === 'drums';
        const options = isDrums
            ? '<option value="rhythm_deep">Deep Rhythm</option>'
            : '<option value="chord_chordnet_2e1d">ChordNet</option><option value="chord_btc_sl">BTC-SL</option><option value="rhythm_deep">Deep Rhythm</option>';

        const row = document.createElement('div');
        row.className = 'analysis-config-row';
        row.dataset.track = track;
        row.innerHTML = `
            <span class="track-color" style="background:${TRACK_COLORS[track]}"></span>
            <span class="track-name">${TRACK_LABELS[track]}</span>
            <select>${options}</select>
            <button class="btn-run">run</button>
            <span class="status-badge pending">pending</span>
        `;
        row.querySelector('.btn-run').addEventListener('click', async () => {
            if (!state.currentWid || state.analysisRunning.has(track)) return;
            const plugin = row.querySelector('select').value;
            await api.triggerAnalysis(state.currentWid, track, plugin);
        });
        list.appendChild(row);
    }
}

function onAnalysisStarted(track) {
    state.analysisRunning.add(track);
    const badge = document.querySelector(`.analysis-config-row[data-track="${track}"] .status-badge`);
    if (badge) { badge.className = 'status-badge running'; badge.textContent = 'running'; }
    const btn = document.querySelector(`.analysis-config-row[data-track="${track}"] .btn-run`);
    if (btn) btn.disabled = true;
}

function onAnalysisDone(payload) {
    const { track, result } = payload;
    state.analysisRunning.delete(track);
    state.analysisResults[track] = result;
    const badge = document.querySelector(`.analysis-config-row[data-track="${track}"] .status-badge`);
    if (badge) { badge.className = 'status-badge done'; badge.textContent = 'done'; }

    const allDone = [...state.selectedTracks].every(t => state.analysisResults[t]);
    const btn = document.getElementById('btn-run-all');
    if (allDone) {
        btn.textContent = 'all done';
        btn.disabled = true;
    } else {
        btn.textContent = 'run all';
        btn.disabled = false;
    }
    renderStep();
}

// ══════════════════════════════════════
//  Step 3: OUTPUT (Timeline)
// ══════════════════════════════════════

async function buildOutputTimeline() {
    const container = document.getElementById('track-timeline-list');
    const tracks = [...state.selectedTracks];
    if (tracks.length === 0) { container.innerHTML = '<p class="empty-msg">no tracks selected</p>'; return; }

    container.innerHTML = '';
    stopPlayback();
    state.duration = 30;

    for (const track of tracks) {
        const row = document.createElement('div');
        row.className = 'track-row';
        row.innerHTML = `
            <div class="track-label">
                <span class="t-name">${TRACK_LABELS[track]}</span>
                <input type="range" class="vol-slider" min="0" max="100" value="100" data-track="${track}">
            </div>
            <div class="track-content">
                <div class="track-waveform"><canvas data-track="${track}"></canvas></div>
                <div class="track-chords" data-track="${track}"></div>
            </div>
        `;
        container.appendChild(row);

        // Load viz data
        let vizData = state.trackVizData[track];
        if (!vizData) {
            try {
                vizData = await api.getVisualization(state.currentWid, track);
                state.trackVizData[track] = vizData;
            } catch {
                vizData = { waveform: { peaks: [] }, beats: null, chords: null, metadata: { duration: 30 } };
            }
        }
        if (vizData.metadata?.duration) state.duration = vizData.metadata.duration;

        const canvas = row.querySelector('canvas');
        renderTrackCanvas(canvas, track, vizData);
        renderChordBlocks(row.querySelector('.track-chords'), vizData, track);

        // Click to seek
        canvas.parentElement.addEventListener('click', e => {
            const rect = canvas.getBoundingClientRect();
            state.currentTime = ((e.clientX - rect.left) / rect.width) * state.duration;
            document.getElementById('seek-bar').value = state.currentTime;
            renderAllTracks();
            updateTimeDisplay();
        });
    }

    document.getElementById('seek-bar').max = state.duration;
}

function renderTrackCanvas(canvas, track, vizData) {
    const peaks = vizData.waveform?.peaks || [];
    drawTimeline(canvas, peaks, vizData.beats, vizData.chords, {
        duration: state.duration,
        waveColor: TRACK_COLORS[track],
    });
    drawPlayhead(canvas, state.currentTime / state.duration);
}

function renderChordBlocks(container, vizData, track) {
    container.innerHTML = '';
    if (!vizData.chords) return;
    for (const c of vizData.chords) {
        const pct = (c.durationProportion || (c.duration / state.duration)) * 100;
        const block = document.createElement('div');
        block.className = 'chord-block';
        block.style.width = pct + '%';
        block.textContent = c.name || '';
        container.appendChild(block);
    }
}

function renderAllTracks() {
    document.querySelectorAll('.track-waveform canvas').forEach(canvas => {
        const track = canvas.dataset.track;
        const vizData = state.trackVizData[track];
        if (vizData) renderTrackCanvas(canvas, track, vizData);
    });
}

// ══════════════════════════════════════
//  Playback
// ══════════════════════════════════════

function bindPlayback() {
    document.getElementById('btn-play').addEventListener('click', togglePlay);
    document.getElementById('seek-bar').addEventListener('input', () => {
        state.currentTime = parseFloat(document.getElementById('seek-bar').value);
        updateTimeDisplay();
        renderAllTracks();
    });
}

function bindSpeedCycle() {
    const speeds = [1, 0.5, 0.75, 1.25, 1.5, 2];
    let idx = 0;
    document.getElementById('speed-label').addEventListener('click', () => {
        idx = (idx + 1) % speeds.length;
        state.speed = speeds[idx];
        document.getElementById('speed-label').textContent = 'x' + speeds[idx];
    });
}

function togglePlay() {
    state.playing ? pausePlayback() : startPlayback();
}

function startPlayback() {
    state.playing = true;
    state.lastTs = performance.now();
    document.getElementById('btn-play').innerHTML = '<svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="2" width="3.5" height="12" rx="0.5"/><rect x="9.5" y="2" width="3.5" height="12" rx="0.5"/></svg>';
    tick();
}

function pausePlayback() {
    state.playing = false;
    document.getElementById('btn-play').innerHTML = '<svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5v11l9-5.5z"/></svg>';
    if (state.raf) cancelAnimationFrame(state.raf);
}

function stopPlayback() {
    pausePlayback();
    state.currentTime = 0;
    document.getElementById('seek-bar').value = 0;
    updateTimeDisplay();
}

function tick() {
    if (!state.playing) return;
    const now = performance.now();
    state.currentTime += (now - state.lastTs) / 1000 * state.speed;
    state.lastTs = now;
    if (state.currentTime >= state.duration) { state.currentTime = state.duration; pausePlayback(); }
    document.getElementById('seek-bar').value = state.currentTime;
    renderAllTracks();
    updateTimeDisplay();
    if (state.playing) state.raf = requestAnimationFrame(tick);
}

function updateTimeDisplay() {
    document.getElementById('time-display').textContent = `${fmt(state.currentTime)}/${fmt(state.duration)}`;
}

function fmt(s) {
    return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

// ══════════════════════════════════════
//  Override goToStep to trigger separation
// ══════════════════════════════════════

const _origGoToStep = goToStep;
// Patch: trigger separation when entering step 2 for the first time
// ── Util ──
function esc(s) { const el = document.createElement('span'); el.textContent = s; return el.innerHTML; }
