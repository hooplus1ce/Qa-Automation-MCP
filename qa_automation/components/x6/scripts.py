"""AntV X6 browser-side evaluation scripts for Playwright."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 扫描并绑定 X6 Graph 实例
# ---------------------------------------------------------------------------
BIND_X6 = r"""
var container = document.querySelector('.x6-graph');
if (!container) return JSON.stringify({ bound: false, reason: '未找到 .x6-graph 画布容器' });

if (window.__x6_graph && typeof window.__x6_graph.getNodes === 'function') {
    var rect = container.getBoundingClientRect();
    return JSON.stringify({
        bound: true,
        source: 'cached',
        zoom: window.__x6_graph.zoom(),
        translate: window.__x6_graph.translate(),
        containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
    });
}

// 检查全局 window 属性
for (var k in window) {
    try {
        if (window[k] && typeof window[k].getNodes === 'function' && typeof window[k].getEdges === 'function') {
            window.__x6_graph = window[k];
            var rect = container.getBoundingClientRect();
            return JSON.stringify({
                bound: true,
                source: 'window.' + k,
                zoom: window.__x6_graph.zoom(),
                translate: window.__x6_graph.translate(),
                containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
            });
        }
    } catch(e) {}
}

// 扫描 React Fiber 寻找 stateNode.graph
var fiberKey = Object.keys(container).find(function(k) { return k.startsWith('__reactInternalInstance$') || k.startsWith('__reactFiber$'); });
if (fiberKey) {
    var cur = container[fiberKey];
    for (var i = 0; i < 25 && cur; i++) {
        var inst = cur.stateNode;
        if (inst) {
            if (inst.graph && typeof inst.graph.getNodes === 'function') {
                window.__x6_graph = inst.graph;
                var rect = container.getBoundingClientRect();
                return JSON.stringify({
                    bound: true,
                    source: 'fiber:stateNode.graph@depth' + i,
                    zoom: window.__x6_graph.zoom(),
                    translate: window.__x6_graph.translate(),
                    containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
                });
            }
            for (var m in inst) {
                try {
                    if (inst[m] && typeof inst[m].getNodes === 'function' && typeof inst[m].getEdges === 'function') {
                        window.__x6_graph = inst[m];
                        var rect = container.getBoundingClientRect();
                        return JSON.stringify({
                            bound: true,
                            source: 'fiber:stateNode.' + m + '@depth' + i,
                            zoom: window.__x6_graph.zoom(),
                            translate: window.__x6_graph.translate(),
                            containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
                        });
                    }
                } catch(e2) {}
            }
        }
        cur = cur.return;
    }
}

// 兜底：DOM 节点存在即使无内部 Fiber 引用
var rect = container.getBoundingClientRect();
return JSON.stringify({
    bound: true,
    source: 'dom-only',
    zoom: 1,
    translate: { tx: 0, ty: 0 },
    containerRect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height }
});
"""

# ---------------------------------------------------------------------------
# 提取图模型拓扑
# ---------------------------------------------------------------------------
EXTRACT_GRAPH = r"""
var g = window.__x6_graph;
var container = document.querySelector('.x6-graph');
if (!container) return JSON.stringify({ ok: false, reason: '未找到 .x6-graph' });
var cRect = container.getBoundingClientRect();

var nodes = [];
var edges = [];
var zoom = 1;
var translate = { tx: 0, ty: 0 };

if (g && typeof g.getNodes === 'function') {
    zoom = g.zoom();
    translate = g.translate();
    var cap = function (v) {
        if (v === null || v === undefined) return v;
        if (typeof v === 'string') return v.length > 200 ? v.slice(0, 200) : v;
        if (typeof v === 'number' || typeof v === 'boolean') return v;
        if (Array.isArray(v)) return v.map(cap);
        if (typeof v === 'object') {
            var o = {};
            for (var k in v) { try { o[k] = cap(v[k]); } catch (e) {} }
            return o;
        }
        return String(v).slice(0, 200);
    };
    nodes = g.getNodes().map(function(n) {
        var pos = n.getPosition ? n.getPosition() : { x: 0, y: 0 };
        var size = n.getSize ? n.getSize() : { width: 0, height: 0 };
        var data = n.getData ? n.getData() : (n.data || {});
        var ports = n.getPorts ? n.getPorts() : [];
        return {
            id: n.id,
            shape: n.shape,
            position: pos,
            size: size,
            data: cap(data),
            ports: ports
        };
    });
    edges = g.getEdges().map(function(e) {
        return {
            id: e.id,
            source: e.getSourceCellId ? e.getSourceCellId() : e.source,
            target: e.getTargetCellId ? e.getTargetCellId() : e.target,
            sourcePort: e.getSourcePortId ? e.getSourcePortId() : null,
            targetPort: e.getTargetPortId ? e.getTargetPortId() : null
        };
    });
}

return JSON.stringify({
    ok: true,
    zoom: zoom,
    translate: translate,
    containerRect: { x: cRect.left, y: cRect.top, width: cRect.width, height: cRect.height },
    nodes: nodes,
    edges: edges
});
"""

# ---------------------------------------------------------------------------
# 自适应居中与平移补偿
# ---------------------------------------------------------------------------
FIT_VIEW = r"""function(padding) {
    var g = window.__x6_graph;
    if (!g) return JSON.stringify({ ok: false, reason: '未找到 X6 Graph 实例' });
    try {
        if (typeof g.enablePanning === 'function') g.enablePanning();
        if (typeof g.enableMouseWheel === 'function') g.enableMouseWheel();
        if (typeof g.zoomToFit === 'function') {
            g.zoomToFit({ padding: padding || 40, maxScale: 1 });
        } else if (typeof g.centerContent === 'function') {
            g.centerContent();
        }
        return JSON.stringify({
            ok: true,
            zoom: g.zoom(),
            translate: g.translate(),
            panning: typeof g.isPannable === 'function' ? g.isPannable() : true
        });
    } catch(e) {
        return JSON.stringify({ ok: false, error: e.message });
    }
}"""
