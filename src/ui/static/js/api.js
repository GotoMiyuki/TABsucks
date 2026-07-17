/**
 * API 封装层 —— 所有后端通信集中于此。
 *
 * 后端固定端口 8000；通过 :py:func:`src.ui.server.make_app` 暴露。
 *
 * 错误处理：所有方法返回 ``{ok: false, error: "..."}`` 统一格式（HTTP 非 2xx 也解析）。
 */

const api = {
    _baseURL: '/api',

    /** HTTP helper。失败返回 {ok:false, error:string}，不抛。 */
    async _fetchJSON(url, opts = {}) {
        try {
            const res = await fetch(url, opts);
            const text = await res.text();
            let body = null;
            try {
                body = text ? JSON.parse(text) : null;
            } catch (_) {
                return { ok: false, error: `非 JSON 响应 (${res.status})` };
            }
            if (!res.ok) {
                // 后端错误格式：{"detail":{"error":"..."}} 或 {"error":"..."}
                const err = (body && body.detail && body.detail.error)
                    ? body.detail.error
                    : (body && body.error ? body.error : `HTTP ${res.status}`);
                return { ok: false, error: err, status: res.status };
            }
            // 兼容两种风格：{ok: true, ...} 或直接的字段
            if (body && typeof body === 'object' && 'ok' in body) return body;
            return { ok: true, data: body };
        } catch (e) {
            return { ok: false, error: String(e && e.message || e) };
        }
    },

    // ── 车间 ──

    async listWorkshops() {
        return this._fetchJSON(`${this._baseURL}/workshops`);
    },

    async createWorkshop(name = 'New Workshop') {
        return this._fetchJSON(`${this._baseURL}/workshops`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
    },

    async getWorkshopState(wid) {
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}`);
    },

    async updateSelectedTracks(wid, tracks) {
        return this._fetchJSON(
            `${this._baseURL}/workshops/${wid}/selected-tracks`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tracks }),
            }
        );
    },

    async updateCurrentTab(wid, tab) {
        return this._fetchJSON(
            `${this._baseURL}/workshops/${wid}/current-tab`,
            {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tab }),
            }
        );
    },

    async renameWorkshop(wid, name) {
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
    },

    /** 关闭 = deactivate。MusicWorkshop 实例仍在内存；可重新激活。 */
    async closeWorkshop(wid) {
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}/close`, {
            method: 'POST',
        });
    },

    /** 永久删除（内存 + 磁盘）。默认 keep_state=false。 */
    async deleteWorkshop(wid, { keepState = false } = {}) {
        const query = new URLSearchParams({
            keep_state: String(keepState),
        });
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}?${query}`, {
            method: 'DELETE',
        });
    },

    /** 切到某车间（等价 close 旧 + resume_autosave 新）。 */
    async switchWorkshop(wid) {
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}/switch`, {
            method: 'POST',
        });
    },

    async getActiveWorkshop() {
        const r = await this._fetchJSON(`${this._baseURL}/workshops-active`);
        if (!r.ok) return { ok: false, error: r.error };
        return { ok: true, active_id: r.data };
    },

    // ── 插件列表 ──

    async listSeparatorPlugins() {
        return this._fetchJSON(`${this._baseURL}/plugins/separators`);
    },

    async listAnalyzerPlugins() {
        return this._fetchJSON(`${this._baseURL}/plugins/analyzers`);
    },

    // ── 分析 ──

    async uploadAudio(wid, file) {
        const form = new FormData();
        form.append('file', file);
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}/upload`, {
            method: 'POST',
            body: form,
        });
    },

    async uploadFromUrl(wid, url) {
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}/upload-by-url`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });
    },

    async separate(wid, model = 'BS-RoFormer-SW', device = 'gpu') {
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}/separate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model, device }),
        });
    },

    async analyze(wid, track, plugin = 'chord_ismir2019') {
        return this._fetchJSON(`${this._baseURL}/workshops/${wid}/analyze`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track, plugin }),
        });
    },

    async getAnalysisResults(wid) {
        return this._fetchJSON(
            `${this._baseURL}/workshops/${wid}/analysis-results`
        );
    },

    async getVisualization(wid, track = 'full') {
        return this._fetchJSON(
            `${this._baseURL}/workshops/${wid}/visualization?track=${encodeURIComponent(track)}`
        );
    },

    getAudioURL(wid, track) {
        return `${this._baseURL}/workshops/${wid}/audio/${encodeURIComponent(track)}`;
    },

    async exportMidi(wid, tracks) {
        const query = new URLSearchParams();
        for (const track of tracks) query.append('tracks', track);
        try {
            const response = await fetch(
                `${this._baseURL}/workshops/${wid}/midi?${query}`
            );
            if (!response.ok) {
                const body = await response.json().catch(() => null);
                const error = body?.detail?.error || body?.error || `HTTP ${response.status}`;
                return { ok: false, error };
            }
            const disposition = response.headers.get('content-disposition') || '';
            const match = disposition.match(/filename="?([^";]+)"?/i);
            return {
                ok: true,
                blob: await response.blob(),
                filename: match?.[1] || 'tabsucks-selected.mid',
            };
        } catch (error) {
            return { ok: false, error: String(error?.message || error) };
        }
    },

    // ── Events (SSE) ──

    createEventStream() {
        return new EventSource(`${this._baseURL}/events`);
    },
};

export default api;
