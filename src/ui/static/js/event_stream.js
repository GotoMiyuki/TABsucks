/**
 * SSE 事件流封装。
 * 切换车间时自动断开旧连接、建立新连接。
 */

import api from './api.js?v=20260716b';

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

    /** 连接到指定车间的事件流 */
    connect(wid) {
        this.disconnect();
        this._wid = wid;
        this._source = api.createEventStream(wid);
        this._source.onmessage = (e) => {
            try {
                const event = JSON.parse(e.data);
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
