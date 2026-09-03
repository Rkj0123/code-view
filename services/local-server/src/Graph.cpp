#include "Graph.h"

#include "LegacySemantics.h"

#include <QJsonDocument>
#include <QQueue>
#include <QRegularExpression>

#include <algorithm>
#include <stdexcept>
#include <tuple>

namespace
{
constexpr int MaxNodes = 500'000;
constexpr int MaxEdges = 2'000'000;

std::runtime_error error(const QString& message)
{
    return std::runtime_error(message.toStdString());
}

QString requiredString(const QJsonObject& object, const QString& key, bool allowEmpty = false)
{
    const QJsonValue value = object.value(key);
    if (!value.isString() || (!allowEmpty && value.toString().isEmpty()))
        throw error(key + " must be a string");
    return value.toString();
}

void exactKeys(const QJsonObject& object, const QSet<QString>& required, const QString& field)
{
    const QStringList keys = object.keys();
    const QSet<QString> actual(keys.begin(), keys.end());
    if (actual != required)
        throw error(field + " has missing or unknown keys");
}

void nullableString(const QJsonObject& object, const QString& key)
{
    const QJsonValue value = object.value(key);
    if (!value.isNull() && !value.isString())
        throw error(key + " must be a string or null");
}

void validateRange(const QJsonValue& value)
{
    if (value.isNull())
        return;
    if (!value.isObject())
        throw error("range must be an object or null");
    const QJsonObject range = value.toObject();
    exactKeys(range, {"start", "end"}, "range");
    QJsonObject positions[2];
    int positionIndex = 0;
    for (const QString part: {"start", "end"})
    {
        const QJsonObject position = range.value(part).toObject();
        exactKeys(position, {"line", "column"}, "range position");
        if (position.value("line").toInteger(0) < 1 || position.value("column").toInteger(0) < 1)
            throw error("range positions must be one-based positive integers");
        positions[positionIndex++] = position;
    }
    const qint64 startLine = positions[0].value("line").toInteger();
    const qint64 endLine = positions[1].value("line").toInteger();
    if (endLine < startLine ||
        (endLine == startLine && positions[1].value("column").toInteger() < positions[0].value("column").toInteger()))
        throw error("range end precedes start");
}

QString optionalPath(const QJsonObject& object, const RepositoryPath& paths)
{
    const QJsonValue value = object.value("path");
    if (value.isNull())
        return {};
    if (!value.isString())
        throw error("path must be a string or null");
    const QString relative = paths.relative(value.toString());
    paths.resolve(relative);
    return relative;
}

QStringList sortedValues(const QMultiHash<QString, QString>& values, const QString& key)
{
    QStringList result = values.values(key);
    std::sort(result.begin(), result.end());
    return result;
}

QString rangeKey(const QJsonValue& value)
{
    if (value.isNull())
        return {};
    const QJsonObject range = value.toObject();
    QStringList parts;
    for (const QString& side: {"start", "end"})
        for (const QString& coordinate: {"line", "column"})
            parts.append(QString::number(range.value(side).toObject().value(coordinate).toInteger()));
    return parts.join(':');
}

bool nodeLess(const QJsonObject& left, const QJsonObject& right)
{
    const auto key = [](const QJsonObject& node) {
        return std::tuple(node.value("qualifiedName").toString(), node.value("kind").toString(), node.value("id").toString());
    };
    return key(left) < key(right);
}

bool edgeLess(const QJsonObject& left, const QJsonObject& right)
{
    const auto key = [](const QJsonObject& edge) {
        return std::tuple(edge.value("kind").toString(), edge.value("source").toString(),
                          edge.value("target").toString(), edge.value("path").toString(), rangeKey(edge.value("range")));
    };
    return key(left) < key(right);
}

bool diagnosticLess(const QJsonObject& left, const QJsonObject& right)
{
    const auto key = [](const QJsonObject& diagnostic) {
        return std::tuple(diagnostic.value("path").toString(), rangeKey(diagnostic.value("range")),
                          diagnostic.value("code").toString(), diagnostic.value("message").toString());
    };
    return key(left) < key(right);
}
}

GraphSnapshot GraphSnapshot::fromNdjson(const QByteArray& data, const RepositoryPath& paths)
{
    GraphSnapshot graph;
    bool metaSeen = false;
    bool summarySeen = false;
    int phase = 0;
    QJsonObject previousNode;
    QJsonObject previousEdge;
    QJsonObject previousDiagnostic;
    int expectedNodes = -1;
    int expectedEdges = -1;
    int expectedDiagnostics = -1;
    const QRegularExpression nodeIdPattern("^n_[0-9a-f]{24}$");
    const QRegularExpression edgeIdPattern("^e_[0-9a-f]{24}$");
    const QSet<QString> edgeKinds = {
        "contains", "imports", "calls", "inherits", "constructs", "reads", "writes",
        "decorates", "type_uses", "api_calls", "test_covers"};

    const QList<QByteArray> lines = data.split('\n');
    for (int index = 0; index < lines.size(); ++index)
    {
        const QByteArray line = lines.at(index).trimmed();
        if (line.isEmpty())
            continue;
        if (line.size() > 1'000'000)
            throw error(QString("NDJSON line %1 exceeds 1 MB").arg(index + 1));
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(line, &parseError);
        if (parseError.error != QJsonParseError::NoError || !document.isObject())
            throw error(QString("invalid NDJSON object at line %1").arg(index + 1));
        const QJsonObject record = document.object();
        const QString recordKind = requiredString(record, "record");
        if (recordKind == "meta")
        {
            if (metaSeen || !graph.m_nodes.isEmpty() || !graph.m_edges.isEmpty())
                throw error("meta must be the first record");
            if (record.value("schemaVersion").toString() != "code-view.graph/v1")
                throw error("unsupported graph schemaVersion");
            if (!record.value("project").isObject())
                throw error("meta.project must be an object");
            exactKeys(record, {"record", "schemaVersion", "project"}, "meta record");
            graph.m_project = record.value("project").toObject();
            exactKeys(graph.m_project, {"root", "languages", "entryNodeId", "entryReachableNodeIds"}, "project");
            if (graph.m_project.value("root").toString() != ".")
                throw error("project.root must be '.'");
            if (!graph.m_project.value("languages").isArray())
                throw error("project.languages must be an array");
            for (const QJsonValue& language: graph.m_project.value("languages").toArray())
                if (!language.isString())
                    throw error("project.languages must contain strings");
            nullableString(graph.m_project, "entryNodeId");
            metaSeen = true;
            phase = 1;
        }
        else if (recordKind == "node")
        {
            if (!metaSeen || summarySeen || phase != 1 || !record.value("node").isObject())
                throw error("invalid node record position or envelope");
            QJsonObject node = record.value("node").toObject();
            exactKeys(record, {"record", "node"}, "node record");
            exactKeys(node, {"id", "kind", "language", "name", "qualifiedName", "path", "range", "signature",
                             "docstring", "external", "unresolved", "reason", "modifiers"}, "node");
            const QString id = requiredString(node, "id");
            if (!nodeIdPattern.match(id).hasMatch() || graph.m_nodes.contains(id))
                throw error("invalid or duplicate node id: " + id);
            const QString kind = requiredString(node, "kind");
            static const QSet<QString> nodeKinds = {"repository", "package", "file", "module", "class", "function",
                                                    "method", "variable", "external", "unresolved"};
            if (!nodeKinds.contains(kind))
                throw error("unsupported node kind: " + kind);
            legacyNodeKind(kind); // The public string remains canonical; this validates adapter coverage in tests.
            const QString name = requiredString(node, "name", true);
            const QString qualifiedName = requiredString(node, "qualifiedName", true);
            const QString path = optionalPath(node, paths);
            validateRange(node.value("range"));
            nullableString(node, "language");
            nullableString(node, "signature");
            nullableString(node, "docstring");
            if (!node.value("external").isBool() || !node.value("unresolved").isBool() ||
                !node.value("modifiers").isArray())
                throw error("node flags and modifiers are required");
            for (const QJsonValue& modifier: node.value("modifiers").toArray())
                if (!modifier.isString())
                    throw error("node.modifiers must contain strings");
            const bool external = node.value("external").toBool();
            const bool unresolved = node.value("unresolved").toBool();
            if (external != (kind == "external") || unresolved != (kind == "unresolved"))
                throw error("node boundary flags must match its kind");
            const bool boundary = external || unresolved;
            const QJsonValue reason = node.value("reason");
            if (boundary && (!reason.isString() || reason.toString().isEmpty()))
                throw error("external and unresolved nodes require a non-empty reason");
            if (!boundary && !reason.isNull())
                throw error("internal nodes must use a null reason");
            if (!previousNode.isEmpty() && nodeLess(node, previousNode))
                throw error("node records must be sorted");
            previousNode = node;
            graph.m_nodes.insert(id, {id, kind, name, qualifiedName, path, node});
            if (!path.isEmpty())
                graph.m_files.insert(path);
            if (graph.m_nodes.size() > MaxNodes)
                throw error("graph exceeds node limit");
        }
        else if (recordKind == "edge")
        {
            if (!metaSeen || summarySeen || phase > 2 || !record.value("edge").isObject())
                throw error("invalid edge record position or envelope");
            phase = 2;
            QJsonObject edge = record.value("edge").toObject();
            exactKeys(record, {"record", "edge"}, "edge record");
            exactKeys(edge, {"id", "kind", "language", "source", "target", "path", "range", "reason"}, "edge");
            const QString id = requiredString(edge, "id");
            if (!edgeIdPattern.match(id).hasMatch() || graph.m_edges.contains(id))
                throw error("invalid or duplicate edge id: " + id);
            const QString kind = requiredString(edge, "kind");
            if (!edgeKinds.contains(kind) || legacyEdgeKind(kind) == Edge::EDGE_UNDEFINED)
                throw error("unsupported edge kind: " + kind);
            const QString source = requiredString(edge, "source");
            const QString target = requiredString(edge, "target");
            requiredString(edge, "language");
            nullableString(edge, "reason");
            const QString path = optionalPath(edge, paths);
            validateRange(edge.value("range"));
            if (!previousEdge.isEmpty() && edgeLess(edge, previousEdge))
                throw error("edge records must be sorted");
            previousEdge = edge;
            graph.m_edges.insert(id, {id, kind, source, target, path, edge});
            if (graph.m_edges.size() > MaxEdges)
                throw error("graph exceeds edge limit");
        }
        else if (recordKind == "diagnostic")
        {
            if (!metaSeen || summarySeen || phase > 3 || !record.value("diagnostic").isObject())
                throw error("invalid diagnostic record position or envelope");
            phase = 3;
            QJsonObject diagnostic = record.value("diagnostic").toObject();
            exactKeys(record, {"record", "diagnostic"}, "diagnostic record");
            exactKeys(diagnostic, {"code", "severity", "message", "path", "range"}, "diagnostic");
            requiredString(diagnostic, "code");
            const QString severity = requiredString(diagnostic, "severity");
            if (severity != "info" && severity != "warning" && severity != "error")
                throw error("diagnostic severity is unsupported");
            requiredString(diagnostic, "message", true);
            optionalPath(diagnostic, paths);
            validateRange(diagnostic.value("range"));
            if (!previousDiagnostic.isEmpty() && diagnosticLess(diagnostic, previousDiagnostic))
                throw error("diagnostic records must be sorted");
            previousDiagnostic = diagnostic;
            graph.m_diagnostics.append(diagnostic);
        }
        else if (recordKind == "summary")
        {
            if (!metaSeen || summarySeen || phase > 3)
                throw error("invalid summary record");
            exactKeys(record, {"record", "nodeCount", "edgeCount", "diagnosticCount"}, "summary record");
            expectedNodes = record.value("nodeCount").toInt(-1);
            expectedEdges = record.value("edgeCount").toInt(-1);
            expectedDiagnostics = record.value("diagnosticCount").toInt(-1);
            summarySeen = true;
            phase = 4;
        }
        else
            throw error("unknown NDJSON record: " + recordKind);
    }

    if (!metaSeen || !summarySeen)
        throw error("NDJSON requires meta and summary records");
    if (expectedNodes != graph.m_nodes.size() || expectedEdges != graph.m_edges.size() ||
        expectedDiagnostics != graph.m_diagnostics.size())
        throw error("summary counts do not match records");
    graph.rebuildIndexes();
    const QString entry = graph.m_project.value("entryNodeId").toString();
    if (!entry.isEmpty() && !graph.m_nodes.contains(entry))
        throw error("project entryNodeId is unknown");
    const QJsonValue reachable = graph.m_project.value("entryReachableNodeIds");
    if (!reachable.isArray())
        throw error("project.entryReachableNodeIds must be an array");
    QSet<QString> reachableIds;
    for (const QJsonValue value: reachable.toArray())
    {
        if (!value.isString() || !graph.m_nodes.contains(value.toString()))
            throw error("project.entryReachableNodeIds contains an unknown node");
        if (reachableIds.contains(value.toString()))
            throw error("project.entryReachableNodeIds contains a duplicate node");
        reachableIds.insert(value.toString());
    }
    if (!entry.isEmpty() && !reachableIds.contains(entry))
        throw error("project.entryNodeId is missing from entryReachableNodeIds");
    return graph;
}

void GraphSnapshot::rebuildIndexes()
{
    m_outgoing.clear();
    m_incoming.clear();
    m_files.clear();
    for (const GraphNode& node: m_nodes)
        if (!node.path.isEmpty())
            m_files.insert(node.path);
    for (const GraphEdge& edge: m_edges)
    {
        if (!m_nodes.contains(edge.source) || !m_nodes.contains(edge.target))
            throw error("dangling edge: " + edge.id);
        m_outgoing.insert(edge.source, edge.id);
        m_incoming.insert(edge.target, edge.id);
    }
}

QJsonObject GraphSnapshot::view(const QString& name) const
{
    if (name != "repository" && name != "entry")
        throw error("view must be repository or entry");
    QSet<QString> selected;
    if (name == "repository")
    {
        selected = QSet<QString>(m_nodes.keyBegin(), m_nodes.keyEnd());
    }
    else
    {
        for (const QJsonValue value: m_project.value("entryReachableNodeIds").toArray())
            selected.insert(value.toString());
        QQueue<QString> pending;
        for (const QString& id: selected)
            pending.enqueue(id);
        while (!pending.isEmpty())
        {
            const QString child = pending.dequeue();
            for (const QString& edgeId: m_incoming.values(child))
            {
                const GraphEdge& edge = m_edges[edgeId];
                if (edge.kind == "contains" && !selected.contains(edge.source))
                {
                    selected.insert(edge.source);
                    pending.enqueue(edge.source);
                }
            }
        }
    }
    QStringList nodeIds = selected.values();
    std::sort(nodeIds.begin(), nodeIds.end());
    QStringList edgeIds = m_edges.keys();
    std::sort(edgeIds.begin(), edgeIds.end());
    QJsonArray nodes;
    QJsonArray edges;
    for (const QString& id: nodeIds)
        nodes.append(m_nodes[id].json);
    for (const QString& id: edgeIds)
    {
        const GraphEdge& edge = m_edges[id];
        if (selected.contains(edge.source) && selected.contains(edge.target))
            edges.append(edge.json);
    }
    return {
        {"schemaVersion", "code-view.graph/v1"},
        {"project", m_project},
        {"nodes", nodes},
        {"edges", edges},
        {"diagnostics", m_diagnostics},
    };
}

QJsonObject GraphSnapshot::graph(
    const QString& root, int depth, const QString& direction, int limit, const QSet<QString>& kinds) const
{
    if (!m_nodes.contains(root))
        throw error("unknown root node");
    if (depth < 0 || depth > 8)
        throw error("depth must be between 0 and 8");
    if (direction != "in" && direction != "out" && direction != "both")
        throw error("direction must be in, out, or both");
    if (limit < 1 || limit > 1'000)
        throw error("limit must be between 1 and 1000");

    QSet<QString> selected = {root};
    QStringList order = {root};
    QQueue<QPair<QString, int>> queue;
    queue.enqueue({root, 0});
    QSet<QString> frontier;
    while (!queue.isEmpty())
    {
        const auto [nodeId, currentDepth] = queue.dequeue();
        if (currentDepth == depth)
            continue;
        QStringList edgeIds;
        if (direction != "in")
            edgeIds.append(sortedValues(m_outgoing, nodeId));
        if (direction != "out")
            edgeIds.append(sortedValues(m_incoming, nodeId));
        std::sort(edgeIds.begin(), edgeIds.end());
        edgeIds.removeDuplicates();
        for (const QString& edgeId: edgeIds)
        {
            const GraphEdge& edge = m_edges[edgeId];
            if (!kinds.isEmpty() && !kinds.contains(edge.kind))
                continue;
            const QString other = edge.source == nodeId ? edge.target : edge.source;
            if (selected.contains(other))
                continue;
            if (selected.size() >= limit)
            {
                frontier.insert(other);
                continue;
            }
            selected.insert(other);
            order.push_back(other);
            queue.enqueue({other, currentDepth + 1});
        }
    }

    QJsonArray nodes;
    for (const QString& id: order)
        nodes.append(m_nodes[id].json);
    QStringList allEdgeIds = m_edges.keys();
    std::sort(allEdgeIds.begin(), allEdgeIds.end());
    QJsonArray edges;
    for (const QString& id: allEdgeIds)
    {
        const GraphEdge& edge = m_edges[id];
        if (selected.contains(edge.source) && selected.contains(edge.target) &&
            (kinds.isEmpty() || kinds.contains(edge.kind)))
            edges.append(edge.json);
    }
    QStringList frontierIds = frontier.values();
    std::sort(frontierIds.begin(), frontierIds.end());
    return {
        {"nodes", nodes},
        {"edges", edges},
        {"truncated", !frontier.isEmpty()},
        {"frontierNodeIds", QJsonArray::fromStringList(frontierIds)},
    };
}

QJsonObject GraphSnapshot::node(
    const QString& id, int incomingCursor, int outgoingCursor, int limit, bool showAll) const
{
    if (!m_nodes.contains(id))
        throw error("unknown node");
    if (incomingCursor < 0 || outgoingCursor < 0 || limit < 1 || limit > 500)
        throw error("invalid node pagination");
    QJsonObject result = m_nodes[id].json;
    const QStringList incomingIds = sortedValues(m_incoming, id);
    const QStringList outgoingIds = sortedValues(m_outgoing, id);
    QJsonArray consumers;
    QJsonArray dependencies;
    QJsonArray incomingEdges;
    QJsonArray outgoingEdges;
    const int incomingLength = showAll ? incomingIds.size() - incomingCursor : limit;
    const int outgoingLength = showAll ? outgoingIds.size() - outgoingCursor : limit;
    const QStringList incomingPage = incomingIds.mid(incomingCursor, incomingLength);
    const QStringList outgoingPage = outgoingIds.mid(outgoingCursor, outgoingLength);
    for (const QString& edgeId: incomingPage)
    {
        const GraphEdge& edge = m_edges[edgeId];
        consumers.append(m_nodes[edge.source].json);
        incomingEdges.append(edge.json);
    }
    for (const QString& edgeId: outgoingPage)
    {
        const GraphEdge& edge = m_edges[edgeId];
        dependencies.append(m_nodes[edge.target].json);
        outgoingEdges.append(edge.json);
    }
    result.insert("incomingCount", incomingIds.size());
    result.insert("outgoingCount", outgoingIds.size());
    result.insert("consumers", consumers);
    result.insert("dependencies", dependencies);
    result.insert("incomingEdges", incomingEdges);
    result.insert("outgoingEdges", outgoingEdges);
    const int nextIncoming = incomingCursor + incomingPage.size();
    const int nextOutgoing = outgoingCursor + outgoingPage.size();
    result.insert("nextIncomingCursor", nextIncoming < incomingIds.size() ? QJsonValue(nextIncoming) : QJsonValue());
    result.insert("nextOutgoingCursor", nextOutgoing < outgoingIds.size() ? QJsonValue(nextOutgoing) : QJsonValue());
    return result;
}

QJsonArray GraphSnapshot::search(const QString& query, int limit) const
{
    if (query.isEmpty() || query.size() > 200 || limit < 1 || limit > 100)
        throw error("invalid search query or limit");
    QList<GraphNode> matches;
    for (const GraphNode& node: m_nodes)
    {
        if (node.name.contains(query, Qt::CaseInsensitive) ||
            node.qualifiedName.contains(query, Qt::CaseInsensitive))
            matches.push_back(node);
    }
    std::sort(matches.begin(), matches.end(), [&query](const GraphNode& left, const GraphNode& right) {
        const bool leftPrefix = left.name.startsWith(query, Qt::CaseInsensitive);
        const bool rightPrefix = right.name.startsWith(query, Qt::CaseInsensitive);
        if (leftPrefix != rightPrefix)
            return leftPrefix;
        if (left.qualifiedName != right.qualifiedName)
            return left.qualifiedName < right.qualifiedName;
        return left.id < right.id;
    });
    QJsonArray result;
    for (const GraphNode& node: matches.mid(0, limit))
        result.append(node.json);
    return result;
}

QSet<QString> GraphSnapshot::parseErrorPaths() const
{
    QSet<QString> paths;
    for (const QJsonValue& value: m_diagnostics)
    {
        const QJsonObject diagnostic = value.toObject();
        if (diagnostic.value("code").toString() == "PY_SYNTAX_ERROR" &&
            diagnostic.value("severity").toString() == "error" &&
            diagnostic.value("path").isString())
            paths.insert(diagnostic.value("path").toString());
    }
    return paths;
}

GraphSnapshot GraphSnapshot::preservingParseErrorsFrom(const GraphSnapshot& lastGood) const
{
    const QSet<QString> stalePaths = parseErrorPaths();
    if (stalePaths.isEmpty())
        return *this;

    GraphSnapshot merged = *this;
    QSet<QString> replacedNodeIds;
    for (auto it = merged.m_nodes.begin(); it != merged.m_nodes.end();)
    {
        if (stalePaths.contains(it->path))
        {
            replacedNodeIds.insert(it.key());
            it = merged.m_nodes.erase(it);
        }
        else
            ++it;
    }
    for (auto it = merged.m_edges.begin(); it != merged.m_edges.end();)
    {
        if (stalePaths.contains(it->path) || replacedNodeIds.contains(it->source) || replacedNodeIds.contains(it->target))
            it = merged.m_edges.erase(it);
        else
            ++it;
    }

    QSet<QString> staleNodeIds;
    for (const GraphNode& original: lastGood.m_nodes)
    {
        if (!stalePaths.contains(original.path))
            continue;
        GraphNode stale = original;
        QJsonArray modifiers = stale.json.value("modifiers").toArray();
        if (!modifiers.contains("stale"))
            modifiers.append("stale");
        stale.json.insert("modifiers", modifiers);
        staleNodeIds.insert(stale.id);
        merged.m_nodes.insert(stale.id, std::move(stale));
    }
    for (const GraphEdge& edge: lastGood.m_edges)
    {
        if ((stalePaths.contains(edge.path) || staleNodeIds.contains(edge.source) || staleNodeIds.contains(edge.target)) &&
            merged.m_nodes.contains(edge.source) && merged.m_nodes.contains(edge.target))
            merged.m_edges.insert(edge.id, edge);
    }

    QSet<QString> reachable;
    for (const QJsonValue& value: merged.m_project.value("entryReachableNodeIds").toArray())
        if (merged.m_nodes.contains(value.toString()))
            reachable.insert(value.toString());
    for (const QJsonValue& value: lastGood.m_project.value("entryReachableNodeIds").toArray())
        if (merged.m_nodes.contains(value.toString()))
            reachable.insert(value.toString());
    QStringList ordered = reachable.values();
    std::sort(ordered.begin(), ordered.end());
    merged.m_project.insert("entryReachableNodeIds", QJsonArray::fromStringList(ordered));
    const QString entry = merged.m_project.value("entryNodeId").toString();
    if (!entry.isEmpty() && !merged.m_nodes.contains(entry))
        merged.m_project.insert("entryNodeId", lastGood.m_project.value("entryNodeId"));
    merged.rebuildIndexes();
    return merged;
}

namespace
{
QJsonObject overlay(
    const QHash<QString, GraphNode>& current,
    const QHash<QString, GraphNode>& base)
{
    QSet<QString> ids(current.keyBegin(), current.keyEnd());
    ids.unite(QSet<QString>(base.keyBegin(), base.keyEnd()));
    QStringList ordered = ids.values();
    std::sort(ordered.begin(), ordered.end());
    QJsonArray added, removed, modified, unchanged;
    for (const QString& id: ordered)
    {
        if (!base.contains(id))
            added.append(QJsonObject{{"id", id}, {"current", current[id].json}});
        else if (!current.contains(id))
            removed.append(QJsonObject{{"id", id}, {"base", base[id].json}});
        else if (current[id].json != base[id].json)
            modified.append(QJsonObject{{"id", id}, {"base", base[id].json}, {"current", current[id].json}});
        else
            unchanged.append(QJsonObject{{"id", id}, {"current", current[id].json}});
    }
    return {{"added", added}, {"removed", removed}, {"modified", modified}, {"unchanged", unchanged},
            {"counts", QJsonObject{{"added", added.size()}, {"removed", removed.size()},
                                    {"modified", modified.size()}, {"unchanged", unchanged.size()}}}};
}

QJsonObject overlay(
    const QHash<QString, GraphEdge>& current,
    const QHash<QString, GraphEdge>& base)
{
    QSet<QString> ids(current.keyBegin(), current.keyEnd());
    ids.unite(QSet<QString>(base.keyBegin(), base.keyEnd()));
    QStringList ordered = ids.values();
    std::sort(ordered.begin(), ordered.end());
    QJsonArray added, removed, modified, unchanged;
    for (const QString& id: ordered)
    {
        if (!base.contains(id))
            added.append(QJsonObject{{"id", id}, {"current", current[id].json}});
        else if (!current.contains(id))
            removed.append(QJsonObject{{"id", id}, {"base", base[id].json}});
        else if (current[id].json != base[id].json)
            modified.append(QJsonObject{{"id", id}, {"base", base[id].json}, {"current", current[id].json}});
        else
            unchanged.append(QJsonObject{{"id", id}, {"current", current[id].json}});
    }
    return {{"added", added}, {"removed", removed}, {"modified", modified}, {"unchanged", unchanged},
            {"counts", QJsonObject{{"added", added.size()}, {"removed", removed.size()},
                                    {"modified", modified.size()}, {"unchanged", unchanged.size()}}}};
}
}

QJsonObject GraphSnapshot::compareTo(const GraphSnapshot& base) const
{
    return {{"nodes", overlay(m_nodes, base.m_nodes)}, {"edges", overlay(m_edges, base.m_edges)}};
}
