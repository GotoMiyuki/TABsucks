/** playback controller: shared state, explicit host callbacks. */
import api from './api.js?v=20260926p2';
import { state, TRACKS, TRACK_LABELS, TRACK_COLORS } from './app_state.js?v=20260926p2';
import { drawWaveform } from './waveform.js?v=20260926p2';
import { calculateTimelineLayout, clampTimelineZoom } from './timeline_zoom.js?v=20260926p2';

export function createPlaybackController({ showToast, requestTab, allSelectedTracksAnalyzed }) {
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
    return { bindPlayback, bindSpeedCycle, loadTab4, syncTab4ZoomControls, destroyTab4Playback, pausePlayback };
}
