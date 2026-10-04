import api from './api.js?v=20261002v1';
import {state, TRACKS, TRACK_LABELS, TRACK_COLORS} from './app_state.js?v=20261002v1';
import {drawWaveform} from './waveform.js?v=20261002v1';
import {calculateTimelineLayout, clampTimelineZoom} from './timeline_zoom.js?v=20261002v1';
import {driftCorrection} from './playback_sync.js?v=20261002v1';
import {$, escapeHTML, formatTime, askDialog} from './ui.js?v=20261002v1';

export function createPlaybackController({showToast, requestTab, onSelectTrack}) {
    const sessions = new Map();
    let generation = 0, playEpoch = 0, pendingPlay = false, heartbeat = null;
    let versions = {}, failed = new Set();
    let downloadBusy = false, restoredVersions = null;
    let exportMenuSignature = '';
    let visualizationLoading = false;
    let audioContext = null;
    const mediaNodes = new Map();

    function bindPlayback() {
        $('btn-play').addEventListener('click', togglePlay);
        $('seek-bar').addEventListener('input', e => setPlaybackTime(e.target.value));
        $('tab4-zoom').addEventListener('input', e => setTab4Zoom(e.target.value));
        $('btn-zoom-in').addEventListener('click', () => setTab4Zoom(state.timelineZoom + .5));
        $('btn-zoom-out').addEventListener('click', () => setTab4Zoom(state.timelineZoom - .5));
        $('tab4-zoom-label').addEventListener('click', () => setTab4Zoom(1));
        $('source-full').addEventListener('click', () => switchSource('full'));
        $('source-stems').addEventListener('click', () => switchSource('stems'));
        $('btn-export-midi').addEventListener('click', exportSelectedTracksMidi);
        $('btn-continue-tab2').addEventListener('click', () => requestTab(2));
        $('follow-playhead').addEventListener('change', e => { state.follow = e.target.checked; });
        $('tab4-track-list').addEventListener('wheel', () => { state.follow = false; $('follow-playhead').checked = false; }, {passive:true});
        $('tab4-track-list').addEventListener('pointerdown', e => {
            if (e.target === $('tab4-track-list')) { state.follow = false; $('follow-playhead').checked = false; }
        });
        $('btn-loop-a').addEventListener('click', () => { state.loop.a = state.currentTime; state.loop.enabled = false; updateLoop(); });
        $('btn-loop-b').addEventListener('click', () => {
            if (state.loop.a === null || state.currentTime <= state.loop.a) { showToast('请先设 A，并在 A 之后的位置设 B', 'warning'); return; }
            state.loop.b = Math.min(state.currentTime, state.duration); updateLoop();
        });
        $('btn-loop').addEventListener('click', () => { state.loop.enabled = !state.loop.enabled; updateLoop(); });
        $('btn-loop-clear').addEventListener('click', () => { state.loop = {a:null,b:null,enabled:false}; updateLoop(); });
        new ResizeObserver(() => applyTab4Zoom()).observe($('tab4-track-list'));
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => requestAnimationFrame(renderTab4Waveforms));
        document.addEventListener('visibilitychange', () => { if (!document.hidden) updatePlaybackUi(); });
    }

    function bindSpeedCycle() {
        $('speed-label').addEventListener('change', e => {
            state.speed = Number(e.target.value);
            for (const audio of state.audioElements.values()) { audio.playbackRate = state.speed; audio.preservesPitch = true; }
        });
        document.addEventListener('keydown', e => {
            if (e.code !== 'Space' || e.repeat || $('action-dialog').open || !state.hasRawAudio || e.target.closest('input,select,textarea,button,summary,a,[contenteditable]')) return;
            e.preventDefault(); void togglePlay();
        });
        $('btn-play').title = '播放 / 暂停（空格；输入时不触发）';
    }

    async function loadTab4() {
        const wid = state.currentWid;
        if (!wid || !state.hasRawAudio) return;
        const token = ++generation;
        visualizationLoading = true;
        updateControls();
        const tracks = ['full', ...(state.separated && !state.separating ? state.availableTracks : [])];
        const data = {};
        if (!$('tab4-track-list').querySelector('.tab4-track-row')) {
            $('tab4-track-list').innerHTML = '<p class="empty-msg" role="status">正在准备音轨与波形…</p>';
        }
        for (const track of tracks) {
            let response = await api.getVisualization(wid, track, state.resultSelections[track]);
            if (state.currentWid !== wid || token !== generation) return;
            if (!response.ok && response.status === 409 && state.resultSelections[track]) {
                delete state.resultSelections[track];
                showToast(`${TRACK_LABELS[track]}所选结果已失效，已刷新最新结果，请核对后再导出`, 'warning');
                response = await api.getVisualization(wid, track);
                if (state.currentWid !== wid || token !== generation) return;
            }
            if (!response.ok) { data[track] = {error:response.error}; continue; }
            data[track] = response.data || response;
        }
        if (state.currentWid !== wid || token !== generation) return;
        state.trackVizData = data;
        if (restoredVersions && Object.entries(restoredVersions).some(([track,version]) => data[track]?.metadata?.sourceVersion !== version)) {
            state.currentTime = 0; state.mix = {}; state.loop = {a:null,b:null,enabled:false};
        }
        restoredVersions = null;
        state.duration = Math.max(0, ...Object.values(data).map(item => Number(item.metadata?.duration || 0)));
        $('info-duration').textContent = state.duration ? `时长 ${formatTime(state.duration)}` : '时长不可用';
        $('info-samplerate').textContent = data.full?.metadata?.sampleRate ? `采样率 ${data.full.metadata.sampleRate} Hz` : '音源信息不可用';
        for (const [track, audio] of state.audioElements) {
            const version = data[track]?.metadata?.sourceVersion;
            if (!version || version !== versions[track]) {
                mediaNodes.get(audio)?.disconnect(); mediaNodes.delete(audio);
                audio.pause(); audio.removeAttribute('src'); audio.load(); audio.remove();
                state.audioElements.delete(track); delete versions[track]; failed.delete(track);
            }
        }
        if (state.source === 'stems' && tracks.length === 1) { pausePlayback(); state.source = 'full'; }
        state.currentTime = Math.min(state.currentTime, state.duration);
        if (state.loop.b > state.duration || state.loop.a >= state.duration) state.loop = {a:null,b:null,enabled:false};
        createTab4AudioElements(wid, tracks);
        renderTab4Tracks(tracks);
        visualizationLoading = false;
        updateControls();
    }

    function renderTab4Tracks(tracks) {
        const container = $('tab4-track-list');
        const scroll = {top:container.scrollTop,left:container.scrollLeft};
        container.replaceChildren();
        const ruler = document.createElement('div'); ruler.className = 'timeline-ruler';
        ruler.innerHTML = '<div class="ruler-label">时间 / 分钟:秒</div><div class="ruler-times"></div>';
        container.append(ruler);
        const scale = ruler.querySelector('.ruler-times');
        for (let i = 0; i <= 10; i++) { const span = document.createElement('span'); span.style.left = `${i*10}%`; span.textContent = formatTime(state.duration*i/10); scale.append(span); }
        for (const track of tracks) {
            const data = state.trackVizData[track];
            const row = document.createElement('div'); row.className = 'tab4-track-row'; row.dataset.track = track;
            row.style.setProperty('--track-color', TRACK_COLORS[track] || 'var(--accent)');
            const label = document.createElement('div'); label.className = 'tab4-track-label';
            label.innerHTML = `<div class="track-name"><span class="track-dot"></span>${TRACK_LABELS[track]}</div>`;
            if (track !== 'full') {
                const mix = state.mix[track] ||= {volume:1,mute:false,solo:false};
                const controls = document.createElement('div'); controls.className = 'track-mix';
                for (const [property, text] of [['mute','静音'],['solo','独奏']]) {
                    const button = document.createElement('button'); button.className = 'btn-secondary'; button.textContent = text;
                    button.setAttribute('aria-label', `${TRACK_LABELS[track]}${text}`); button.setAttribute('aria-pressed', String(mix[property]));
                    button.onclick = () => { mix[property] = !mix[property]; button.setAttribute('aria-pressed', String(mix[property])); applyMix(); }; controls.append(button);
                }
                label.append(controls);
                const volume = document.createElement('input'); volume.type = 'range'; volume.min = 0; volume.max = 1; volume.step = .01; volume.value = mix.volume; volume.className = 'track-volume';
                volume.setAttribute('aria-label', `${TRACK_LABELS[track]}音量`); volume.title = `${Math.round(mix.volume*100)}%`;
                volume.oninput = () => { mix.volume = Number(volume.value); volume.title = `${Math.round(mix.volume*100)}%`; applyMix(); }; label.append(volume);
                const actions = document.createElement('div'); actions.className = 'track-actions';
                const selection = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; check.checked = state.selectedTracks.has(track); check.disabled = state.selectionSaving || state.separating;
                check.addEventListener('change', () => onSelectTrack(track)); selection.append(check, document.createTextNode('加入分析')); actions.append(selection);
                if (data?.metadata?.hasAudioData) {
                    const link = document.createElement('a'); link.textContent = '下载 ↗'; link.href = api.getAudioURL(state.currentWid, track, true, data.metadata.sourceVersion);
                    link.addEventListener('click', e => { e.preventDefault(); void downloadTrack(track); }); actions.append(link);
                }
                label.append(actions);
                if (data?.metadata?.availableResults?.length > 1) {
                    const resultSelect = document.createElement('select');
                    resultSelect.className = 'result-select'; resultSelect.dataset.track = track;
                    resultSelect.setAttribute('aria-label', `${TRACK_LABELS[track]}显示的和弦结果`);
                    const latest = document.createElement('option'); latest.value = ''; latest.textContent = '显示最新和弦结果'; resultSelect.append(latest);
                    data.metadata.availableResults.forEach((result,index) => {
                        const option = document.createElement('option'); option.value = result.id;
                        option.textContent = `${result.plugin} · 结果 ${data.metadata.availableResults.length-index}`;
                        resultSelect.append(option);
                    });
                    resultSelect.value = state.resultSelections[track] || '';
                    resultSelect.onchange = () => { state.resultSelections[track] = resultSelect.value; void loadTab4(); };
                    label.append(resultSelect);
                }
            } else { const note = document.createElement('small'); note.textContent = '与分轨互斥试听'; label.append(note); }
            const content = document.createElement('div'); content.className = 'tab4-time-content';
            if (!data?.metadata?.hasAudioData) {
                content.innerHTML = `<p class="tab4-track-error">${escapeHTML(data?.error || '音频缺失或无法读取，请重新导入或分离')}</p>`;
            } else {
                content.addEventListener('click', e => { const rect = content.getBoundingClientRect(); setPlaybackTime((e.clientX-rect.left)/rect.width*state.duration); });
                const layer = document.createElement('div'); layer.className = 'tab4-waveform-layer';
                const canvas = document.createElement('canvas'); canvas.dataset.track = track; layer.append(canvas);
                if (track !== 'full' && data.metadata.result) {
                    const origin = document.createElement('span'); origin.className = 'result-origin';
                    const prior = state.analyzerSelections[track] && state.analyzerSelections[track] !== data.metadata.result.plugin;
                    origin.textContent = `和弦来源：${data.metadata.result.plugin}${prior ? ' · 保留此前结果' : ''}`;
                    layer.append(origin);
                }
                const chord = document.createElement('div'); chord.className = 'tab4-chord-layer';
                renderChordBlocks(chord, data.chords || [], track);
                const region = document.createElement('div'); region.className = 'loop-region';
                const playhead = document.createElement('div'); playhead.className = 'tab4-playhead'; content.append(region,layer,chord,playhead);
            }
            row.append(label,content); container.append(row);
        }
        $('tab4-track-count').textContent = `${tracks.length-1} 条分轨`;
        applyTab4Zoom(); container.scrollTop = scroll.top; container.scrollLeft = scroll.left;
    }

    function renderChordBlocks(layer, chords, track) {
        if (!chords.length || state.duration <= 0 || track === 'full') {
            const empty = document.createElement('div'); empty.className = 'tab4-chord-empty';
            empty.textContent = track === 'full' ? '原曲对照' : state.analysisRunning.has(track) ? '正在分析，试听仍可用' : state.analysisErrors[track] || '尚未分析 · 加入分析后查看和弦'; layer.append(empty); return;
        }
        for (const chord of chords) {
            const start = Math.max(0, Number(chord.start)), end = Math.min(state.duration, Number(chord.end));
            if (!Number.isFinite(start) || end <= start) continue;
            const block = document.createElement('button'); block.type = 'button'; block.className = 'tab4-chord-block';
            block.style.left = `${start/state.duration*100}%`; block.style.width = `${(end-start)/state.duration*100}%`;
            block.textContent = chord.name || '?'; block.title = `${block.textContent} · ${formatTime(start)}–${formatTime(end)} · ${state.trackVizData[track].metadata.result?.plugin || ''}`;
            block.onclick = e => { e.stopPropagation(); setPlaybackTime(start); }; layer.append(block);
        }
    }

    function renderTab4Waveforms() {
        const style = getComputedStyle(document.documentElement);
        document.querySelectorAll('#tab4-track-list canvas').forEach(canvas => {
            const track = canvas.dataset.track;
            drawWaveform(canvas, state.trackVizData[track]?.waveform?.peaks || [], {color:style.getPropertyValue(`--wave-${track}`).trim() || style.getPropertyValue('--accent').trim(), bgColor:style.getPropertyValue('--wave-bg').trim()});
        }); updatePlaybackUi();
    }
    function setTab4Zoom(value) { state.timelineZoom = clampTimelineZoom(value); syncTab4ZoomControls(); applyTab4Zoom(true); }
    function syncTab4ZoomControls() { $('tab4-zoom').value = state.timelineZoom; $('tab4-zoom-label').textContent = `${state.timelineZoom}×`; }
    function applyTab4Zoom(center = false) {
        const container = $('tab4-track-list'), label = container.querySelector('.tab4-track-label');
        if (!label || !container.clientWidth) return;
        const layout = calculateTimelineLayout({viewportWidth:container.clientWidth,labelWidth:label.getBoundingClientRect().width,zoom:state.timelineZoom,currentTime:state.currentTime,duration:state.duration});
        container.style.setProperty('--tab4-content-width', `${layout.contentWidth}px`);
        if (center) container.scrollLeft = layout.scrollLeft;
        renderTab4Waveforms();
    }

    function createTab4AudioElements(wid, tracks) {
        for (const track of tracks) {
            if (state.audioElements.has(track) || !state.trackVizData[track]?.metadata?.hasAudioData) continue;
            const audio = new Audio(); audio.preload = 'metadata'; audio.preservesPitch = true; audio.playbackRate = state.speed;
            versions[track] = state.trackVizData[track].metadata.sourceVersion;
            audio.src = api.getAudioURL(wid,track,false,versions[track]);
            audio.addEventListener('error', () => {
                if (state.audioElements.get(track) !== audio) return;
                failed.add(track); audio.pause();
                showToast(`${TRACK_LABELS[track]}加载失败，该音轨已停用`, 'error'); updateControls();
            });
            audio.addEventListener('ended', () => {
                if (state.loop.enabled && state.playing) { setPlaybackTime(state.loop.a); void Promise.allSettled(activeAudio().map(a => a.play())); }
                else if (activeAudio().every(a => a.ended || a.paused)) { state.currentTime = state.duration; pausePlayback(); }
            });
            state.audioElements.set(track,audio);
            audio.dataset.track = track; $('audio-engine').append(audio);
        } applyMix();
    }
    function activeAudio() { return [...state.audioElements].filter(([track]) => !failed.has(track) && (state.source === 'full' ? track === 'full' : track !== 'full')).map(([,audio]) => audio); }
    function applyMix() {
        const solo = state.availableTracks.some(track => state.mix[track]?.solo && !failed.has(track));
        for (const [track,audio] of state.audioElements) {
            const mix = state.mix[track] || {volume:1,mute:false,solo:false};
            audio.volume = Math.max(0,Math.min(1,Number(mix.volume)));
            audio.muted = track === 'full' ? state.source !== 'full' : state.source !== 'stems' || mix.mute || (solo && !mix.solo);
        }
    }
    async function switchSource(source) {
        if (source === state.source) return;
        const resume = state.playing || pendingPlay; pausePlayback(); state.source = source; applyMix(); updateControls();
        if (resume) await togglePlay();
    }
    function ready(audio) {
        if (audio.readyState >= 3) return Promise.resolve();
        return new Promise((resolve,reject) => {
            const finish = error => { clearTimeout(timer); audio.removeEventListener('canplay',done); audio.removeEventListener('error',bad); error ? reject(error) : resolve(); };
            const done = () => finish(), bad = () => finish(new Error('音源无法加载'));
            const timer = setTimeout(() => finish(new Error('音源加载超时')),15000);
            audio.addEventListener('canplay',done); audio.addEventListener('error',bad);
            audio.preload = 'auto'; if (audio.networkState === 0) audio.load();
        });
    }
    async function togglePlay() {
        if (state.playing || pendingPlay) { pausePlayback(); return; }
        if (!state.currentWid || state.busy) return;
        let audios = activeAudio();
        if (!audios.length) { showToast('没有可播放的音源', 'warning'); return; }
        const epoch = ++playEpoch; pendingPlay = true; updatePlaybackUi();
        // Use one output clock for all stems rather than independent hardware sinks.
        try {
            audioContext ||= new window.AudioContext();
            for (const audio of audios) if (!mediaNodes.has(audio)) {
                const node = audioContext.createMediaElementSource(audio);
                node.connect(audioContext.destination); mediaNodes.set(audio, node);
            }
            await audioContext.resume();
        } catch (error) { pausePlayback(); showToast(`音频输出未能启动：${error.message}`, 'error'); return; }
        if (epoch !== playEpoch) return;
        const loads = await Promise.allSettled(audios.map(ready));
        if (epoch !== playEpoch) return;
        const usable = audios.filter((audio,index) => loads[index].status === 'fulfilled');
        if (usable.length !== audios.length) {
            for (const [track,audio] of state.audioElements) if (audios.includes(audio) && !usable.includes(audio)) failed.add(track);
            if (!usable.length || !(await askDialog('部分音轨加载失败', '<p>失败音轨已停用。是否播放剩余可用音轨？</p>', '播放可用音轨'))) { pausePlayback(); return; }
            if (epoch !== playEpoch) return;
        }
        if (state.currentTime >= state.duration-.02) state.currentTime = state.loop.enabled ? state.loop.a : 0;
        synchronizeAudioTime(state.currentTime);
        const plays = await Promise.allSettled(usable.map(audio => { audio.playbackRate = state.speed; return audio.play(); }));
        if (epoch !== playEpoch) { usable.forEach(a => a.pause()); return; }
        pendingPlay = false;
        if (plays.some(p => p.status === 'rejected')) { pausePlayback(); showToast('播放未能完整启动，请重新尝试', 'error'); return; }
        state.playing = true; applyMix(); heartbeat = setInterval(syncPlayback,25); state.raf = requestAnimationFrame(tick); updatePlaybackUi();
    }
    function pausePlayback() {
        ++playEpoch; pendingPlay = false; state.playing = false;
        state.audioElements.forEach(audio => audio.pause());
        cancelAnimationFrame(state.raf); clearInterval(heartbeat); heartbeat = null; updatePlaybackUi();
    }
    function synchronizeAudioTime(time) { for (const audio of activeAudio()) { audio.playbackRate = state.speed; if (audio.readyState >= 1) audio.currentTime = Math.min(time,Number.isFinite(audio.duration) ? audio.duration : time); } }
    function setPlaybackTime(time) { state.currentTime = Math.max(0,Math.min(state.duration,Number(time)||0)); synchronizeAudioTime(state.currentTime); updatePlaybackUi(); }
    function syncPlayback() {
        if (!state.playing) return;
        const audios = activeAudio(), master = audios.find(a => !a.paused && !a.ended);
        if (!master) { if (!state.loop.enabled) pausePlayback(); return; }
        state.currentTime = master.currentTime;
        if (state.loop.enabled && state.currentTime >= state.loop.b) { setPlaybackTime(state.loop.a); return; }
        if (master.playbackRate !== state.speed) master.playbackRate = state.speed;
        for (const audio of audios) if (audio !== master && !audio.paused) {
            const correction = driftCorrection(state.currentTime, audio.currentTime, state.speed);
            if (correction.seek !== null) audio.currentTime = correction.seek;
            if (Math.abs(audio.playbackRate - correction.rate) > .001) audio.playbackRate = correction.rate;
        }
    }
    function tick() { if (!state.playing) return; updatePlaybackUi(); state.raf = requestAnimationFrame(tick); }
    function updateLoop() {
        const valid = state.loop.a !== null && state.loop.b !== null && state.loop.b > state.loop.a;
        if (!valid) state.loop.enabled = false;
        $('btn-loop').disabled = !valid; $('btn-loop').setAttribute('aria-pressed',String(state.loop.enabled));
        $('loop-label').textContent = `A ${state.loop.a === null ? '—' : formatTime(state.loop.a)} / B ${state.loop.b === null ? '—' : formatTime(state.loop.b)}`;
        document.querySelectorAll('.loop-region').forEach(region => {
            region.hidden = !valid; region.style.left = `${(state.loop.a||0)/state.duration*100}%`; region.style.width = `${((state.loop.b||0)-(state.loop.a||0))/state.duration*100}%`;
        });
    }
    function updatePlaybackUi() {
        $('seek-bar').max = Math.max(0,state.duration); $('seek-bar').value = state.currentTime;
        $('time-display').textContent = `${formatTime(state.currentTime)} / ${formatTime(state.duration)}`;
        $('btn-play').textContent = pendingPlay ? '…' : state.playing ? 'Ⅱ' : '▶';
        $('btn-play').setAttribute('aria-label', pendingPlay ? '停止等待' : state.playing ? '暂停' : '播放');
        const proportion = state.duration > 0 ? state.currentTime/state.duration : 0;
        document.querySelectorAll('.tab4-playhead').forEach(el => { el.style.left = `${proportion*100}%`; });
        if (state.playing && state.follow) {
            const container = $('tab4-track-list'), content = container.querySelector('.tab4-time-content'), label = container.querySelector('.tab4-track-label');
            if (content && label) {
                const pos = proportion*content.getBoundingClientRect().width;
                const visible = container.clientWidth-label.getBoundingClientRect().width;
                if (pos > container.scrollLeft+visible*.85 || pos < container.scrollLeft) container.scrollLeft = Math.max(0,pos-visible*.3);
            }
        }
    }
    function updateControls() {
        $('source-full').setAttribute('aria-pressed',String(state.source === 'full'));
        $('source-stems').setAttribute('aria-pressed',String(state.source === 'stems'));
        $('source-stems').disabled = !state.separated || state.separating || !state.availableTracks.length;
        $('btn-play').disabled = state.busy || !activeAudio().length;
        $('speed-label').value = state.speed; $('follow-playhead').checked = state.follow;
        document.querySelectorAll('.result-origin').forEach(origin => {
            const track = origin.closest('.tab4-track-row').dataset.track;
            const plugin = state.trackVizData[track]?.metadata?.result?.plugin;
            const prior = state.analyzerSelections[track] && state.analyzerSelections[track] !== plugin;
            origin.textContent = `和弦来源：${plugin}${prior ? ' · 保留此前结果' : ''}`;
        });
        document.querySelectorAll('.track-actions input').forEach(check => { check.checked = state.selectedTracks.has(check.closest('.tab4-track-row').dataset.track); check.disabled = state.selectionSaving || state.separating; });
        $('btn-export-midi').disabled = state.busy || state.selectionSaving || visualizationLoading || !TRACKS.some(track => eligible(track));
        const downloads = state.separating ? [] : TRACKS.filter(track => state.trackVizData[track]?.metadata?.hasAudioData);
        $('audio-export-menu').hidden = !downloads.length;
        const signature = `${state.currentWid}:${downloads.join(',')}`;
        if (signature !== exportMenuSignature) {
            exportMenuSignature = signature;
            $('audio-export-list').replaceChildren();
            for (const track of downloads) {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'btn-text';
                button.textContent = `下载${TRACK_LABELS[track]}`;
                button.onclick = () => { $('audio-export-menu').open = false; void downloadTrack(track); };
                $('audio-export-list').append(button);
            }
        }
        syncTab4ZoomControls(); updateLoop(); updatePlaybackUi();
    }
    function eligible(track) { const item = state.trackVizData[track]; return state.selectedTracks.has(track) && !state.analysisRunning.has(track) && item?.metadata?.result?.id && item.chords?.length && item.metadata.hasAudioData && !state.separating; }
    async function exportSelectedTracksMidi() {
        if (visualizationLoading || state.busy || state.selectionSaving) return;
        const wid = state.currentWid;
        const rows = TRACKS.filter(track => state.selectedTracks.has(track));
        const choices = rows.map(track => `<label class="export-option"><input type="checkbox" name="tracks" value="${track}" ${eligible(track) ? 'checked' : 'disabled'}> ${TRACK_LABELS[track]}<small>${eligible(track) ? escapeHTML(state.trackVizData[track].metadata.result.plugin) : '尚无有效和弦结果或正在分析'}</small></label>`).join('');
        const dialog = askDialog('导出和弦 MIDI', `<p>选择要导出的音轨。仅导出当前显示的有效和弦结果。</p>${choices}`, '导出');
        const checks = [...$('dialog-content').querySelectorAll('input')];
        const validate = () => { $('dialog-confirm').disabled = !checks.some(c => c.checked && !c.disabled); }; checks.forEach(c => c.addEventListener('change',validate)); validate();
        const form = await dialog; if (!form || wid !== state.currentWid) return;
        const tracks = form.getAll('tracks').filter(track => eligible(track)); if (!tracks.length) return;
        const refs = tracks.map(track => state.trackVizData[track].metadata.result.id);
        const result = await api.exportMidi(wid,tracks,refs);
        if (wid !== state.currentWid) return;
        if (!result.ok) { showToast(`导出失败：${result.error}`, 'error'); await loadTab4(); return; }
        const url = URL.createObjectURL(result.blob); const link = document.createElement('a'); link.href = url; link.download = result.filename; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000); showToast('已开始下载 MIDI', 'success');
    }
    async function downloadTrack(track) {
        if (downloadBusy || state.separating) return;
        downloadBusy = true;
        const wid = state.currentWid, url = api.getAudioURL(wid,track,true,state.trackVizData[track]?.metadata?.sourceVersion);
        try {
            const check = await fetch(url,{method:'HEAD'});
            if (wid !== state.currentWid) return;
            if (!check.ok) { showToast('音轨文件已不可用，请刷新或重新分离', 'error'); return; }
            const link = document.createElement('a'); link.href = url; link.download = ''; link.click(); showToast('已开始下载音轨', 'success');
        } catch (error) { showToast(`下载失败：${error.message}`, 'error'); } finally { downloadBusy = false; }
    }
    function saveSession() { if (state.currentWid) sessions.set(state.currentWid,{time:state.currentTime,speed:state.speed,source:state.source,mix:structuredClone(state.mix),loop:{...state.loop},zoom:state.timelineZoom,follow:state.follow,versions:{...versions},results:{...state.resultSelections}}); }
    function useSeparatedSourceWhenIdle() { if (!state.playing && !pendingPlay) state.source = 'stems'; }
    function restoreSession(wid, defaults = {}) {
        const session = sessions.get(wid); restoredVersions = session?.versions || null; state.currentTime = session?.time || 0; state.speed = session?.speed || 1; state.source = session?.source || (state.separated ? 'stems' : 'full'); state.mix = session?.mix || defaults; state.loop = session?.loop || {a:null,b:null,enabled:false}; state.timelineZoom = session?.zoom || 1; state.follow = session?.follow ?? true;
        state.resultSelections = session?.results || {};
    }
    function destroyTab4Playback({forget = false} = {}) {
        visualizationLoading = false;
        mediaNodes.forEach(node => node.disconnect()); mediaNodes.clear();
        if (audioContext) { void audioContext.close().catch(() => {}); audioContext = null; }
        ++generation; pausePlayback(); state.audioElements.forEach(audio => { audio.removeAttribute('src'); audio.load(); audio.remove(); }); state.audioElements.clear(); versions = {}; failed.clear(); state.trackVizData = {}; state.duration = 0; restoredVersions = null;
        if (forget && state.currentWid) sessions.delete(state.currentWid);
    }
    return {bindPlayback,bindSpeedCycle,loadTab4,syncTab4ZoomControls,destroyTab4Playback,pausePlayback,saveSession,restoreSession,updateControls,useSeparatedSourceWhenIdle};
}
