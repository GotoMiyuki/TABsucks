/**
 * Canvas 波形渲染器。
 * 接收 peaks 数组（0-1），在 canvas 上绘制对称波形。
 */

export function drawWaveform(canvas, peaks, options = {}) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const mid = h / 2;
    const color = options.color || '#5b65ff';
    const bgColor = options.bgColor || 'transparent';

    // 背景
    if (bgColor !== 'transparent') {
        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, w, h);
    }

    if (!peaks || peaks.length === 0) return;

    ctx.fillStyle = color;

    const barW = w / peaks.length;
    const gap = Math.max(0.5, barW * 0.15);

    for (let i = 0; i < peaks.length; i++) {
        const amp = peaks[i] * mid * 0.9;
        const x = i * barW;
        const bw = Math.max(1, barW - gap);

        // 上半
        ctx.fillRect(x, mid - amp, bw, amp);
        // 下半（镜像）
        ctx.fillRect(x, mid, bw, amp);
    }
}

/**
 * 绘制带和弦色块和节拍线的波形（Tab4 用）。
 */
export function drawTimeline(canvas, peaks, beats, chords, options = {}) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const mid = h / 2;
    const duration = options.duration || 30;

    // 背景
    ctx.fillStyle = '#0a0a0a';
    ctx.fillRect(0, 0, w, h);

    // 和弦色块
    const CHORD_COLORS = {
        'C': 'rgba(91,101,255,0.12)', 'D': 'rgba(52,199,89,0.12)', 'E': 'rgba(255,149,0,0.12)',
        'F': 'rgba(175,82,222,0.12)', 'G': 'rgba(255,45,85,0.12)', 'A': 'rgba(142,142,147,0.12)',
        'B': 'rgba(255,255,255,0.06)',
    };
    if (chords) {
        for (const c of chords) {
            const x1 = (c.start / duration) * w;
            const x2 = (c.end / duration) * w;
            ctx.fillStyle = CHORD_COLORS[c.root] || '#f0f0f0';
            ctx.fillRect(x1, 0, x2 - x1, h);
            // 和弦名
            if (x2 - x1 > 30) {
                ctx.fillStyle = 'rgba(255,255,255,0.4)';
                ctx.font = '10px JetBrains Mono, monospace';
                ctx.fillText(c.name, x1 + 4, 12);
            }
        }
    }

    // 节拍线
    if (beats) {
        ctx.strokeStyle = 'rgba(255,255,255,0.06)';
        ctx.lineWidth = 1;
        for (const b of beats) {
            const x = (b.time / duration) * w;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, h);
            ctx.stroke();
            if (b.isDownbeat) {
                ctx.strokeStyle = 'rgba(255,255,255,0.12)';
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, h);
                ctx.stroke();
                ctx.strokeStyle = 'rgba(255,255,255,0.06)';
            }
        }
    }

    // 波形
    if (peaks && peaks.length > 0) {
        const barW = w / peaks.length;
        const gap = Math.max(0.5, barW * 0.15);
        ctx.fillStyle = options.waveColor || '#5b65ff';
        for (let i = 0; i < peaks.length; i++) {
            const amp = peaks[i] * mid * 0.85;
            const x = i * barW;
            const bw = Math.max(1, barW - gap);
            ctx.fillRect(x, mid - amp, bw, amp);
            ctx.fillRect(x, mid, bw, amp);
        }
    }
}

/**
 * 绘制播放头（红色竖线）。
 */
export function drawPlayhead(canvas, proportion) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    const x = proportion * w;

    ctx.save();
    ctx.strokeStyle = '#FF3B30';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
    ctx.restore();
}
