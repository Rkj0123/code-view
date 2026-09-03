#pragma once

#include "Config.h"

#include <QHash>
#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QSet>

struct GraphNode
{
    QString id;
    QString kind;
    QString name;
    QString qualifiedName;
    QString path;
    QJsonObject json;
};

struct GraphEdge
{
    QString id;
    QString kind;
    QString source;
    QString target;
    QString path;
    QJsonObject json;
};

class GraphSnapshot
{
public:
    static GraphSnapshot fromNdjson(const QByteArray& data, const RepositoryPath& paths);

    QJsonObject project() const { return m_project; }
    int nodeCount() const { return m_nodes.size(); }
    int edgeCount() const { return m_edges.size(); }
    int diagnosticCount() const { return m_diagnostics.size(); }
    bool containsFile(const QString& relativePath) const { return m_files.contains(relativePath); }

    QJsonObject view(const QString& name) const;
    QJsonObject graph(
        const QString& root,
        int depth,
        const QString& direction,
        int limit,
        const QSet<QString>& kinds = {}) const;
    QJsonObject node(const QString& id, int incomingCursor, int outgoingCursor, int limit, bool showAll) const;
    QJsonArray search(const QString& query, int limit) const;
    QSet<QString> parseErrorPaths() const;
    GraphSnapshot preservingParseErrorsFrom(const GraphSnapshot& lastGood) const;
    QJsonObject compareTo(const GraphSnapshot& base) const;

private:
    void rebuildIndexes();
    QJsonObject m_project;
    QHash<QString, GraphNode> m_nodes;
    QHash<QString, GraphEdge> m_edges;
    QMultiHash<QString, QString> m_outgoing;
    QMultiHash<QString, QString> m_incoming;
    QJsonArray m_diagnostics;
    QSet<QString> m_files;
};
