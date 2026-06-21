/**
 * 多轨同步时间轴控制器。
 * 管理多条音轨的波形+和弦 Canvas，共享播放头位置。
 */

import { drawTimeline, drawPlayhead } from './waveform.js';

const TRACK_COLORS = {
    vocals: '#0066cc', drums: '#ff9500', bass: '#34c759',
    piano: '#af52de', guitar: '#ff2d55', other: '#8e8e93',
};

export default class MultiTrackTimeline {
    constructor(seekBar, timeDisplay, playBtn, stopBtn, speedSelect) {
        this.seekBar = seekBar;
        this.timeDisplay = timeDisplay;
        this.playBtn = playBtn;
        this.stopBtn = stopBtn;
        this.speedSelect = speedSelect;

        this._tracks = [];        // [{ track, canvas, data }]
        this._playing = false;
        this._currentTime = 0;
        this._duration = 30;
        this._speed = 1;
        this._raf = null;
        this._lastTs = 0;

        this._bindControls();
    }

    /** 注册一条音轨的 canvas */
    addTrack(track, canvas, vizData) {
        this._tracks.push({ track, canvas, data: vizData });
        this._duration = vizData?.metadata?.duration || 30;
        this.seekBar.max = this._duration;
        this._renderTrack(this._tracks[this._tracks.length - 1]);
    }

    /** 清空所有轨道 */
    clear() {
        this._tracks = [];
        this._currentTime = 0;
        this._playing = false;
        this.seekBar.value = 0;
        if (this._raf) cancelAnimationFrame(this._raf);
    }

    _bindControls() {
        this.playBtn.addEventListener('click', () => this.togglePlay());
        this.stopBtn.addEventListener('click', () => this.stop());

        this.seekBar.addEventListener('input', () => {
            this._currentTime = parseFloat(this.seekBar.value);
            this._updateTimeDisplay();
            this._renderAll();
        });

        this.speedSelect.addEventListener('change', () => {
            this._speed = parseFloat(this.speedSelect.value);
        });
    }

    togglePlay() {
        this._playing ? this._pause() : this._play();
    }

    _play() {
        this._playing = true;
        this.playBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><rect x="3" y="2" width="3.5" height="12" rx="0.5"/><rect x="9.5" y="2" width="3.5" height="12" rx="0.5"/></svg>';
        this._lastTs = performance.now();
        this._tick();
    }

    _pause() {
        this._playing = false;
        this.playBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M4 2.5v11l9-5.5z"/></svg>';
        if (this._raf) cancelAnimationFrame(this._raf);
    }

    stop() {
        this._pause();
        this._currentTime = 0;
        this.seekBar.value = 0;
        this._updateTimeDisplay();
        this._renderAll();
    }

    _tick() {
        if (!this._playing) return;
        const now = performance.now();
        const dt = (now - this._lastTs) / 1000 * this._speed;
        this._lastTs = now;
        this._currentTime = Math.min(this._currentTime + dt, this._duration);
        this.seekBar.value = this._currentTime;
        this._updateTimeDisplay();
        this._renderAll();
        if (this._currentTime >= this._duration) { this._pause(); return; }
        this._raf = requestAnimationFrame(() => this._tick());
    }

    _renderAll() {
        for (const t of this._tracks) this._renderTrack(t);
    }

    _renderTrack(entry) {
        const { canvas, data } = entry;
        if (!data) return;
        const peaks = data.waveform?.peaks || [];
        drawTimeline(canvas, peaks, data.beats, data.chords, {
            duration: this._duration,
            waveColor: TRACK_COLORS[entry.track] || '#0066cc',
        });
        const proportion = this._currentTime / this._duration;
        drawPlayhead(canvas, proportion);
    }

    _updateTimeDisplay() {
        this.timeDisplay.textContent = `${fmt(this._currentTime)} / ${fmt(this._duration)}`;
    }
}

function fmt(s) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
}
