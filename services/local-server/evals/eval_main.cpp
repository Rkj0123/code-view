#include "Graph.h"
#include "LegacySemantics.h"

#include <QCoreApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QProcess>

#include <algorithm>
#include <iostream>

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    const bool benchmark = argc == 3 && QString::fromLocal8Bit(argv[1]) == "--benchmark";
    if (argc != 1 && !benchmark)
    {
        std::cerr << "usage: code-view-local-server-eval [--benchmark REPOSITORY]\n";
        return 2;
    }
    const QString root = benchmark ? QString::fromLocal8Bit(argv[2])
                                   : QDir(CODE_VIEW_SOURCE_ROOT).filePath("tests/fixtures/tic-tac-toe");
    const QString analyzer = QDir(CODE_VIEW_SOURCE_ROOT).filePath("services/python-analyzer/analyzer.py");
    QElapsedTimer timer;
    timer.start();
    QProcess process;
    process.setWorkingDirectory(root);
    process.start("python3", {analyzer, root, "--format", "ndjson", "--trace"});
    if (!process.waitForStarted(5'000) || !process.waitForFinished(30'000) || process.exitCode() != 0)
    {
        std::cerr << "periodic eval could not run analyzer: " << process.readAllStandardError().constData() << '\n';
        return 2;
    }
    try
    {
        const GraphSnapshot graph = GraphSnapshot::fromNdjson(process.readAllStandardOutput(), RepositoryPath(root));
        const QJsonObject repository = graph.view("repository");
        const QJsonObject entry = graph.view("entry");
        const qint64 analysisImportMs = timer.elapsed();
        if (benchmark)
        {
            QList<double> adjacency;
            const QJsonArray nodes = repository.value("nodes").toArray();
            for (int index = 0; index < qMin(100, nodes.size()); ++index)
            {
                QElapsedTimer query;
                query.start();
                graph.node(nodes.at(index).toObject().value("id").toString(), 0, 0, 100, false);
                adjacency.append(double(query.nsecsElapsed()) / 1'000'000.0);
            }
            std::sort(adjacency.begin(), adjacency.end());
            const int p95Index = qMax(0, int(adjacency.size() * 0.95) - 1);
            const QJsonObject result{{"eval", "local-server-graph-benchmark"},
                                     {"analysisImportMs", analysisImportMs},
                                     {"adjacencySamples", adjacency.size()},
                                     {"adjacencyP95Ms", adjacency.isEmpty() ? 0.0 : adjacency.at(p95Index)},
                                     {"nodes", graph.nodeCount()}, {"edges", graph.edgeCount()}};
            std::cout << QJsonDocument(result).toJson(QJsonDocument::Compact).constData() << '\n';
            return 0;
        }
        int score = 0;
        score += graph.nodeCount() == repository.value("nodes").toArray().size();
        score += graph.edgeCount() == repository.value("edges").toArray().size();
        score += graph.diagnosticCount() == 0;
        score += !graph.project().value("entryNodeId").isNull() && !entry.value("nodes").toArray().isEmpty();
        score += legacyEdgeKind(u"constructs") == Edge::EDGE_CALL &&
                 legacyEdgeKind(u"test_covers") == Edge::EDGE_USAGE &&
                 legacyNodeKind(u"function") == NODE_FUNCTION;
        const QJsonObject result{{"eval", "local-server-python-fixture"}, {"score", score}, {"threshold", 5},
                                 {"nodes", graph.nodeCount()}, {"edges", graph.edgeCount()},
                                 {"entryNodes", entry.value("nodes").toArray().size()}, {"durationMs", analysisImportMs}};
        std::cout << QJsonDocument(result).toJson(QJsonDocument::Compact).constData() << '\n';
        return score == 5 ? 0 : 1;
    }
    catch (const std::exception& exception)
    {
        std::cerr << "periodic eval rejected analyzer output: " << exception.what() << '\n';
        return 2;
    }
}
