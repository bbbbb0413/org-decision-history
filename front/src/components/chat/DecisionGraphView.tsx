import { useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
  type Node,
  type Edge,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { GraphData, GraphNode as ApiGraphNode } from '../../api/types';
import { Button } from '../ui/Button';
import { Badge } from '../ui/Badge';

interface Props {
  graphData: GraphData;
  onOpenDocument?: (documentId: number) => void;
  title?: string;
  height?: number | string;
}

export function DecisionGraphView({ graphData, onOpenDocument, title, height = 400 }: Props) {
  const [selectedNode, setSelectedNode] = useState<ApiGraphNode | null>(null);

  const { nodes, edges } = useMemo(() => {
    if (!graphData || !graphData.nodes || graphData.nodes.length === 0) {
      return { nodes: [], edges: [] };
    }

    // 1. 노드별 인입/인출 차수 계산으로 계층(Level) 결정
    const inDegrees: Record<string, number> = {};
    const adj: Record<string, string[]> = {};
    graphData.nodes.forEach((n) => {
      inDegrees[n.id] = 0;
      adj[n.id] = [];
    });

    graphData.edges.forEach((e) => {
      if (inDegrees[e.target] !== undefined) inDegrees[e.target]++;
      if (adj[e.source]) adj[e.source].push(e.target);
    });

    // BFS로 각 노드의 레벨 계산
    const levels: Record<string, number> = {};
    const queue: string[] = [];
    graphData.nodes.forEach((n) => {
      if (inDegrees[n.id] === 0) {
        levels[n.id] = 0;
        queue.push(n.id);
      }
    });

    if (queue.length === 0 && graphData.nodes.length > 0) {
      // 순환이 있는 경우 첫 노드를 루트로 설정
      levels[graphData.nodes[0].id] = 0;
      queue.push(graphData.nodes[0].id);
    }

    while (queue.length > 0) {
      const u = queue.shift()!;
      const curLvl = levels[u] ?? 0;
      for (const v of adj[u] ?? []) {
        if (levels[v] === undefined || levels[v] < curLvl + 1) {
          levels[v] = curLvl + 1;
          queue.push(v);
        }
      }
    }

    // 미방문 노드는 기본 0레벨
    graphData.nodes.forEach((n) => {
      if (levels[n.id] === undefined) levels[n.id] = 0;
    });

    // 레벨별 노드 그룹화
    const levelGroups: Record<number, ApiGraphNode[]> = {};
    graphData.nodes.forEach((n) => {
      const lvl = levels[n.id];
      if (!levelGroups[lvl]) levelGroups[lvl] = [];
      levelGroups[lvl].push(n);
    });

    // 2. ReactFlow Node 객체 변환 및 위치(Layout) 산출
    const flowNodes: Node[] = [];
    const NODE_WIDTH = 220;
    const LEVEL_GAP_Y = 140;
    const NODE_GAP_X = 40;

    Object.entries(levelGroups).forEach(([lvlStr, groupNodes]) => {
      const lvl = Number(lvlStr);
      const groupWidth = groupNodes.length * NODE_WIDTH + (groupNodes.length - 1) * NODE_GAP_X;
      const startX = Math.max(50, 450 - groupWidth / 2);

      groupNodes.forEach((n, idx) => {
        const x = startX + idx * (NODE_WIDTH + NODE_GAP_X);
        const y = 40 + lvl * LEVEL_GAP_Y;

        const isDecision = n.label === 'Decision';
        const isConflict = graphData.edges.some(
          (e) => (e.source === n.id || e.target === n.id) && e.relType === 'CONFLICTS_WITH'
        );

        flowNodes.push({
          id: n.id,
          position: { x, y },
          data: {
            label: (
              <div
                style={{
                  padding: '8px 10px',
                  borderRadius: 8,
                  fontSize: 12,
                  lineHeight: 1.3,
                  textAlign: 'left',
                  cursor: 'pointer',
                  border: isConflict
                    ? '2px solid #ef4444'
                    : isDecision
                    ? '2px solid #3b82f6'
                    : '1px solid #94a3b8',
                  background: isConflict
                    ? '#fef2f2'
                    : isDecision
                    ? '#eff6ff'
                    : '#f8fafc',
                  color: '#1e293b',
                  boxShadow: '0 2px 4px rgba(0,0,0,0.06)',
                }}
                onClick={() => setSelectedNode(n)}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: '1px 5px',
                      borderRadius: 4,
                      background:
                        n.label === 'Decision'
                          ? '#2563eb'
                          : n.label === 'Policy'
                          ? '#d97706'
                          : n.label === 'System'
                          ? '#7c3aed'
                          : '#059669',
                      color: '#fff',
                    }}
                  >
                    {n.label === 'Decision' ? '의사결정' : n.label === 'Policy' ? '규정' : n.label === 'System' ? '시스템' : n.label === 'Team' ? '부서' : n.label}
                  </span>
                  {n.properties?.status && (
                    <span style={{ fontSize: 10, color: n.properties.status === 'CURRENT' ? '#16a34a' : '#dc2626', fontWeight: 600 }}>
                      {n.properties.status}
                    </span>
                  )}
                </div>
                <div style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={n.name}>
                  {n.name}
                </div>
                {n.properties?.authoredAt && (
                  <div style={{ fontSize: 10, color: '#64748b', marginTop: 3 }}>
                    {n.properties.authoredAt}
                  </div>
                )}
              </div>
            ),
          },
          style: { width: NODE_WIDTH, background: 'transparent', border: 'none', padding: 0 },
        });
      });
    });

    // 3. ReactFlow Edge 객체 변환 및 스타일링
    const flowEdges: Edge[] = graphData.edges.map((e, idx) => {
      const isConflict = e.relType === 'CONFLICTS_WITH';
      const isDeprecate = e.relType === 'DEPRECATES' || e.relType === 'SUPERSEDES';

      const strokeColor = isConflict ? '#ef4444' : isDeprecate ? '#f97316' : '#3b82f6';
      const labelText = isConflict ? '⚠️ 상충' : e.label || e.relType;

      return {
        id: `e-${e.source}-${e.target}-${idx}`,
        source: e.source,
        target: e.target,
        label: labelText,
        animated: isConflict,
        style: {
          stroke: strokeColor,
          strokeWidth: isConflict ? 2.5 : 1.8,
          strokeDasharray: isConflict ? '6,4' : undefined,
        },
        labelStyle: {
          fontSize: 11,
          fontWeight: 600,
          fill: strokeColor,
        },
        labelBgStyle: {
          fill: '#ffffff',
          fillOpacity: 0.95,
          rx: 4,
          ry: 4,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: strokeColor,
          width: 14,
          height: 14,
        },
      };
    });

    return { nodes: flowNodes, edges: flowEdges };
  }, [graphData]);

  if (!graphData || !graphData.nodes || graphData.nodes.length === 0) {
    return (
      <div style={{ padding: 24, textAlign: 'center', color: '#64748b', background: '#f8fafc', borderRadius: 8, border: '1px solid #e2e8f0' }}>
        연결된 의사결정 인과 관계망(DAG)이 없습니다.
      </div>
    );
  }

  const docIdNum = selectedNode?.properties?.docId ? Number(selectedNode.properties.docId) : (
    selectedNode && !isNaN(Number(selectedNode.id)) ? Number(selectedNode.id) : null
  );

  return (
    <div style={{ position: 'relative', width: '100%', height, border: '1px solid #cbd5e1', borderRadius: 8, overflow: 'hidden', background: '#f8fafc' }}>
      {title && (
        <div style={{ position: 'absolute', top: 10, left: 12, zIndex: 5, background: 'rgba(255,255,255,0.92)', padding: '4px 10px', borderRadius: 6, border: '1px solid #e2e8f0', fontSize: 12, fontWeight: 700, color: '#334155' }}>
          {title}
        </div>
      )}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.3}
        maxZoom={1.5}
        nodesDraggable={true}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#cbd5e1" />
        <Controls showInteractive={false} position="bottom-right" />
        <MiniMap
          nodeColor={(n) => (n.id.startsWith('entity') ? '#94a3b8' : '#3b82f6')}
          style={{ height: 70, width: 100 }}
          position="top-right"
        />
      </ReactFlow>

      {/* 노드 클릭 시 우측/하단 상세 정보 팝오버 */}
      {selectedNode && (
        <div
          style={{
            position: 'absolute',
            bottom: 12,
            left: 12,
            right: 12,
            maxWidth: 460,
            background: '#ffffff',
            border: '1px solid #cbd5e1',
            borderRadius: 8,
            padding: 14,
            boxShadow: '0 10px 15px -3px rgba(0,0,0,0.1)',
            zIndex: 10,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <Badge tone="accent">{selectedNode.label}</Badge>
              {selectedNode.properties?.status && (
                <Badge tone={selectedNode.properties.status === 'CURRENT' ? 'ok' : 'neutral'}>
                  {selectedNode.properties.status}
                </Badge>
              )}
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 16, color: '#64748b' }}
            >
              ✕
            </button>
          </div>

          <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b', marginBottom: 6 }}>
            {selectedNode.name}
          </div>

          {selectedNode.properties?.authoredAt && (
            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>
              작성일: {selectedNode.properties.authoredAt}
            </div>
          )}

          {docIdNum !== null && onOpenDocument && (
            <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
              <Button size="sm" variant="primary" onClick={() => onOpenDocument(docIdNum)}>
                원문 문서 상세 열기 ›
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
