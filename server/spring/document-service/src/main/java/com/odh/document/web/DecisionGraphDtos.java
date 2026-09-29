package com.odh.document.web;

import java.util.List;
import java.util.Map;

public final class DecisionGraphDtos {

    public record GraphNode(
            String id,
            String label,
            String name,
            Map<String, Object> properties
    ) {}

    public record GraphEdge(
            String source,
            String target,
            String relType,
            String label,
            Map<String, Object> properties
    ) {}

    public record GraphResponse(
            List<GraphNode> nodes,
            List<GraphEdge> edges
    ) {}

    private DecisionGraphDtos() {}
}
