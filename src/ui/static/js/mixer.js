/**
 * 混音台 UI。
 * 为每个音轨生成垂直滑块 + M/S 按钮。
 */

const TRACKS = ['vocals', 'drums', 'bass', 'piano', 'guitar', 'other'];
const TRACK_LABELS = { vocals: '人声', drums: '鼓', bass: '贝斯', piano: '钢琴', guitar: '吉他', other: '其他' };

export default class Mixer {
    constructor(container, onStateChange) {
        this.container = container;
        this.onStateChange = onStateChange;
        this._state = {};
        TRACKS.forEach(t => {
            this._state[t] = { volume: 1, mute: false, solo: false };
        });
        this._render();
    }

    _render() {
        this.container.innerHTML = '';
        for (const track of TRACKS) {
            const ch = document.createElement('div');
            ch.className = 'mixer-channel';
            ch.innerHTML = `
                <span class="ch-label">${TRACK_LABELS[track]}</span>
                <input type="range" min="0" max="100" value="100" data-track="${track}" data-role="volume">
                <div class="mixer-btns">
                    <button class="mixer-btn" data-track="${track}" data-role="mute" title="静音">M</button>
                    <button class="mixer-btn" data-track="${track}" data-role="solo" title="独奏">S</button>
                </div>
            `;
            this.container.appendChild(ch);
        }

        this.container.addEventListener('click', (e) => {
            const btn = e.target.closest('.mixer-btn');
            if (!btn) return;
            const track = btn.dataset.track;
            const role = btn.dataset.role;
            const st = this._state[track];
            if (role === 'mute') {
                st.mute = !st.mute;
                btn.classList.toggle('active-m', st.mute);
            } else if (role === 'solo') {
                st.solo = !st.solo;
                btn.classList.toggle('active-s', st.solo);
            }
            this.onStateChange?.(track, { ...st });
        });

        this.container.addEventListener('input', (e) => {
            if (e.target.dataset.role !== 'volume') return;
            const track = e.target.dataset.track;
            this._state[track].volume = parseInt(e.target.value) / 100;
            this.onStateChange?.(track, { ...this._state[track] });
        });
    }

    getState() {
        return { ...this._state };
    }
}
