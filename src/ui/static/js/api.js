/**
 * API 封装层 —— 所有后端通信集中于此。
 * 替换后端实现时只需修改此文件中的 URL 或 mock 标记。
 */

const api = {
    // ── 车间 ──
    async listWorkshops() {
        const res = await fetch('/api/workshops');
        return res.json();
    },

    async createWorkshop(name = '新建车间') {
        const res = await fetch('/api/workshops', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        return res.json();
    },

    async getWorkshop(wid) {
        const res = await fetch(`/api/workshops/${wid}`);
        return res.json();
    },

    async updateWorkshop(wid, data) {
        const res = await fetch(`/api/workshops/${wid}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return res.json();
    },

    async deleteWorkshop(wid) {
        const res = await fetch(`/api/workshops/${wid}`, { method: 'DELETE' });
        return res.json();
    },

    async switchWorkshop(wid) {
        const res = await fetch(`/api/workshops/${wid}/switch`, { method: 'POST' });
        return res.json();
    },

    // ── 分析 ──
    async uploadAudio(wid, file) {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch(`/api/workshops/${wid}/upload`, { method: 'POST', body: form });
        return res.json();
    },

    async triggerSeparation(wid, model = 'BS-RoFormer') {
        const res = await fetch(`/api/workshops/${wid}/separate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model }),
        });
        return res.json();
    },

    async triggerAnalysis(wid, track, plugin = 'chord_chordnet_2e1d') {
        const res = await fetch(`/api/workshops/${wid}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track, plugin }),
        });
        return res.json();
    },

    async getVisualization(wid, track = 'full') {
        const res = await fetch(`/api/workshops/${wid}/visualization?track=${track}`);
        return res.json();
    },

    getAudioUrl(wid, track) {
        return `/api/workshops/${wid}/audio/${track}`;
    },

    // ── SSE ──
    createEventStream(wid) {
        return new EventSource(`/api/workshops/${wid}/events`);
    },
};

export default api;
