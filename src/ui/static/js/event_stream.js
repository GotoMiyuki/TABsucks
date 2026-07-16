/** SSE 全局事件流封装，按当前 active workshop 在前端路由。 */

import api from './api.js?v=20260716g';

export default class EventStream {
    constructor() {
        this._source = null;
        this._handlers = {};
        this._wid = null;
    }

    /** 注册事件处理器 */
    on(type, handler) {
        if (!this._handlers[type]) this._handlers[type] = [];
        this._handlers[type].push(handler);
        return this; // 链式调用
    }

    /** 更新当前接收业务事件的 workshop，不重建全局 SSE 连接。 */
    setWorkshopId(wid) {
        this._wid = wid || null;
    }

    /** 建立唯一的全局事件流连接。 */
    connect(wid = null) {
        this.disconnect();
        this.setWorkshopId(wid);
        this._source = api.createEventStream();
        this._source.onmessage = (e) => {
            try {
                const event = JSON.parse(e.data);
                if (
                    event.workshop_id
                    && event.workshop_id !== this._wid
                ) {
                    return;
                }
                const handlers = this._handlers[event.type] || [];
                handlers.forEach(fn => fn(event.payload));
            } catch (err) {
                console.warn('[EventStream] parse error:', err);
            }
        };
        this._source.onerror = () => {
            console.warn('[EventStream] connection error, browser will auto-reconnect');
        };
    }

    /** 断开当前连接 */
    disconnect() {
        if (this._source) {
            this._source.close();
            this._source = null;
        }
        this._wid = null;
    }
}
