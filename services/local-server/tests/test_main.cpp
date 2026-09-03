#include "Config.h"
#include "Graph.h"
#include "HttpHost.h"
#include "LegacySemantics.h"
#include "Runtime.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFile>
#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QProcess>
#include <QTemporaryDir>
#include <QThread>
#include <QTimer>
#include <QUrlQuery>

#include <cerrno>
#include <csignal>
#include <algorithm>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
int checks = 0;

void check(bool condition, const char* message)
{
    ++checks;
    if (!condition)
        throw std::runtime_error(message);
}

void throws(const std::function<void()>& operation, const char* message)
{
    bool didThrow = false;
    try { operation(); } catch (const std::exception&) { didThrow = true; }
    check(didThrow, message);
}

void writeFile(const QString& path, const QByteArray& data)
{
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate) || file.write(data) != data.size())
        throw std::runtime_error("test fixture write failed");
}

QByteArray hashFile(const QString& path)
{
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly))
        throw std::runtime_error("test fixture read failed");
    return QCryptographicHash::hash(file.readAll(), QCryptographicHash::Sha256);
}

QJsonObject node(
    const QString& id, const QString& kind, const QString& name, const QJsonValue& path = QJsonValue(),
    bool external = false, bool unresolved = false, QJsonValue reason = QJsonValue())
{
    return {{"id", id}, {"kind", kind}, {"language", kind == "repository" ? QJsonValue() : QJsonValue("python")},
            {"name", name}, {"qualifiedName", name}, {"path", path}, {"range", QJsonValue()},
            {"signature", QJsonValue()}, {"docstring", QJsonValue()}, {"external", external},
            {"unresolved", unresolved}, {"reason", reason}, {"modifiers", QJsonArray()}};
}

QJsonObject edge(const QString& id, const QString& kind, const QString& source, const QString& target,
                 const QJsonValue& path = QJsonValue())
{
    return {{"id", id}, {"kind", kind}, {"language", "python"}, {"source", source}, {"target", target},
            {"path", path}, {"range", QJsonValue()}, {"reason", QJsonValue()}};
}

QByteArray ndjson(QJsonArray nodes, QJsonArray edges = {}, QJsonArray diagnostics = {}, QString entry = {})
{
    QJsonArray reachable;
    if (!entry.isEmpty())
        reachable.append(entry);
    QJsonObject project{{"root", "."}, {"languages", QJsonArray{"python"}},
                        {"entryNodeId", entry.isEmpty() ? QJsonValue() : QJsonValue(entry)},
                        {"entryReachableNodeIds", reachable}};
    QByteArray result;
    auto append = [&result](QJsonObject value) {
        result += QJsonDocument(value).toJson(QJsonDocument::Compact) + '\n';
    };
    auto sorted = [](QJsonArray values, auto key) {
        QList<QJsonObject> objects;
        for (const QJsonValue& value: values)
            objects.append(value.toObject());
        std::sort(objects.begin(), objects.end(), [&](const QJsonObject& left, const QJsonObject& right) {
            return key(left) < key(right);
        });
        return objects;
    };
    append({{"record", "meta"}, {"schemaVersion", "code-view.graph/v1"}, {"project", project}});
    for (const QJsonObject& value: sorted(nodes, [](const QJsonObject& node) {
             return std::tuple(node.value("qualifiedName").toString(), node.value("kind").toString(), node.value("id").toString());
         }))
        append({{"record", "node"}, {"node", value}});
    for (const QJsonObject& value: sorted(edges, [](const QJsonObject& item) {
             return std::tuple(item.value("kind").toString(), item.value("source").toString(), item.value("target").toString(),
                               item.value("path").toString());
         }))
        append({{"record", "edge"}, {"edge", value}});
    for (const QJsonObject& value: sorted(diagnostics, [](const QJsonObject& item) {
             return std::tuple(item.value("path").toString(), item.value("code").toString(), item.value("message").toString());
         }))
        append({{"record", "diagnostic"}, {"diagnostic", value}});
    append({{"record", "summary"}, {"nodeCount", nodes.size()}, {"edgeCount", edges.size()},
            {"diagnosticCount", diagnostics.size()}});
    return result;
}

QByteArray run(const QString& program, const QStringList& arguments, const QString& cwd = {})
{
    QProcess process;
    if (!cwd.isEmpty())
        process.setWorkingDirectory(cwd);
    process.start(program, arguments);
    if (!process.waitForStarted(5'000) || !process.waitForFinished(120'000) || process.exitCode() != 0)
        throw std::runtime_error(("test subprocess failed: " + QString::fromUtf8(process.readAllStandardError())).toStdString());
    return process.readAllStandardOutput();
}

bool processGone(qint64 pid)
{
    for (int attempt = 0; attempt < 100; ++attempt)
    {
        errno = 0;
        if (::kill(pid_t(pid), 0) != 0 && errno == ESRCH)
            return true;
        QThread::msleep(10);
    }
    return false;
}

qint64 waitForPid(const QString& path)
{
    for (int attempt = 0; attempt < 200; ++attempt)
    {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 5);
        QFile file(path);
        if (file.open(QIODevice::ReadOnly))
        {
            const qint64 pid = file.readAll().trimmed().toLongLong();
            if (pid > 0)
                return pid;
        }
        QThread::msleep(5);
    }
    throw std::runtime_error("child PID was not published");
}

bool runAsync(AnalyzerRunner& runner)
{
    QEventLoop loop;
    QTimer timer;
    timer.setSingleShot(true);
    bool completed = false;
    bool success = false;
    QObject::connect(&runner, &AnalyzerRunner::generationFinished, &loop, [&](bool value) {
        completed = true;
        success = value;
        loop.quit();
    });
    QObject::connect(&timer, &QTimer::timeout, &loop, &QEventLoop::quit);
    timer.start(3'000);
    runner.requestReindex();
    loop.exec();
    check(completed, "async analyzer test timed out");
    return success;
}

GraphSnapshot analyze(const QString& root)
{
    const QString analyzer = QDir(CODE_VIEW_SOURCE_ROOT).filePath("services/python-analyzer/analyzer.py");
    const QByteArray output = run("python3", {analyzer, root, "--format", "ndjson"}, root);
    return GraphSnapshot::fromNdjson(output, RepositoryPath(root));
}

void graphContractTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"), "def main():\n    return 1\n");
    const QString repo = "n_000000000000000000000001";
    const QString fn = "n_000000000000000000000002";
    const QString relation = "e_000000000000000000000001";
    const QJsonArray nodes{node(repo, "repository", "repo"), node(fn, "function", "main", "main.py")};
    const GraphSnapshot graph = GraphSnapshot::fromNdjson(
        ndjson(nodes, {edge(relation, "contains", repo, fn)}, {}, fn), RepositoryPath(temp.path()));
    check(graph.nodeCount() == 2 && graph.edgeCount() == 1, "valid graph did not import");
    check(graph.view("repository").value("nodes").toArray().size() == 2, "repository view was capped");
    check(graph.view("entry").value("nodes").toArray().size() == 2, "entry view missed containment ancestor");
    check(graph.node(fn, 0, 0, 1, false).value("incomingCount").toInt() == 1, "node consumers missing");

    QJsonObject reversed = node(fn, "function", "main", "main.py");
    reversed.insert("range", QJsonObject{{"start", QJsonObject{{"line", 3}, {"column", 4}}},
                                          {"end", QJsonObject{{"line", 3}, {"column", 3}}}});
    throws([&]() { GraphSnapshot::fromNdjson(ndjson({reversed}), RepositoryPath(temp.path())); },
           "reversed range accepted");
    QJsonObject badBoundary = node(fn, "external", "dep", QJsonValue(), true, false, QJsonValue());
    throws([&]() { GraphSnapshot::fromNdjson(ndjson({badBoundary}), RepositoryPath(temp.path())); },
           "boundary without reason accepted");
    QJsonObject contradictoryBoundary = node(fn, "external", "dep", QJsonValue(), true, true, "outside");
    throws([&]() { GraphSnapshot::fromNdjson(ndjson({contradictoryBoundary}), RepositoryPath(temp.path())); },
           "contradictory boundary flags accepted");
    QJsonObject ordinaryBoundary = node(fn, "function", "main", "main.py", true, false, "outside");
    throws([&]() { GraphSnapshot::fromNdjson(ndjson({ordinaryBoundary}), RepositoryPath(temp.path())); },
           "ordinary node accepted an external flag");
    QJsonObject nonCanonical = node(fn, "function", "main", "pkg/../main.py");
    throws([&]() { GraphSnapshot::fromNdjson(ndjson({nonCanonical}), RepositoryPath(temp.path())); },
           "non-canonical graph path accepted");
    QByteArray duplicateReachable = ndjson(nodes, {}, {}, fn);
    const QByteArray oneReachable = "\"entryReachableNodeIds\":[\"" + fn.toUtf8() + "\"]";
    duplicateReachable.replace(oneReachable, "\"entryReachableNodeIds\":[\"" + fn.toUtf8() + "\",\"" + fn.toUtf8() + "\"]");
    throws([&]() { GraphSnapshot::fromNdjson(duplicateReachable, RepositoryPath(temp.path())); },
           "duplicate entry reachability accepted");
    QByteArray missingEntry = ndjson(nodes, {}, {}, fn);
    missingEntry.replace(oneReachable, "\"entryReachableNodeIds\":[]");
    throws([&]() { GraphSnapshot::fromNdjson(missingEntry, RepositoryPath(temp.path())); },
           "entry omitted from reachability accepted");
    QJsonObject unknown = node(fn, "function", "main", "main.py");
    unknown.insert("extra", 1);
    throws([&]() { GraphSnapshot::fromNdjson(ndjson({unknown}), RepositoryPath(temp.path())); },
           "unknown contract key accepted");
    const QByteArray ordered = ndjson(nodes, {edge(relation, "contains", repo, fn)}, {}, fn);
    QList<QByteArray> records = ordered.trimmed().split('\n');
    std::swap(records[1], records[2]);
    throws([&]() { GraphSnapshot::fromNdjson(records.join('\n') + '\n', RepositoryPath(temp.path())); },
           "unsorted node records accepted");
    records = ordered.trimmed().split('\n');
    std::swap(records[2], records[3]);
    throws([&]() { GraphSnapshot::fromNdjson(records.join('\n') + '\n', RepositoryPath(temp.path())); },
           "interleaved node and edge records accepted");

    check(legacyNodeKind(u"file") == NODE_FILE && legacyNodeKind(u"module") == NODE_MODULE &&
          legacyNodeKind(u"package") == NODE_PACKAGE && legacyNodeKind(u"class") == NODE_CLASS &&
          legacyNodeKind(u"function") == NODE_FUNCTION && legacyNodeKind(u"method") == NODE_METHOD,
          "legacy node mapping changed");
    const QHash<QString, Edge::EdgeType> expected{{"contains", Edge::EDGE_MEMBER}, {"imports", Edge::EDGE_IMPORT},
        {"calls", Edge::EDGE_CALL}, {"inherits", Edge::EDGE_INHERITANCE}, {"constructs", Edge::EDGE_CALL},
        {"reads", Edge::EDGE_USAGE}, {"writes", Edge::EDGE_USAGE}, {"decorates", Edge::EDGE_ANNOTATION_USAGE},
        {"type_uses", Edge::EDGE_TYPE_USAGE}, {"api_calls", Edge::EDGE_CALL}, {"test_covers", Edge::EDGE_USAGE}};
    for (auto it = expected.begin(); it != expected.end(); ++it)
        check(legacyEdgeKind(it.key()) == it.value(), "legacy edge mapping changed");
}

void staleAndDiffTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"), "def main():\n    return 1\n");
    const QString repo = "n_000000000000000000000001";
    const QString fn = "n_000000000000000000000002";
    const GraphSnapshot good = GraphSnapshot::fromNdjson(
        ndjson({node(repo, "repository", "repo"), node(fn, "function", "main", "main.py")},
               {edge("e_000000000000000000000001", "contains", repo, fn)}, {}, fn), RepositoryPath(temp.path()));
    const QJsonObject diagnostic{{"code", "PY_SYNTAX_ERROR"}, {"severity", "error"}, {"message", "bad"},
                                 {"path", "main.py"}, {"range", QJsonObject{{"start", QJsonObject{{"line", 1}, {"column", 1}}},
                                                                                 {"end", QJsonObject{{"line", 1}, {"column", 2}}}}}};
    const GraphSnapshot broken = GraphSnapshot::fromNdjson(
        ndjson({node(repo, "repository", "repo"), node("n_000000000000000000000003", "file", "main.py", "main.py")},
               {}, {diagnostic}, repo), RepositoryPath(temp.path()));
    const GraphSnapshot preserved = broken.preservingParseErrorsFrom(good);
    const QJsonArray staleNodes = preserved.view("repository").value("nodes").toArray();
    bool stale = false;
    for (const QJsonValue& value: staleNodes)
        stale |= value.toObject().value("id").toString() == fn && value.toObject().value("modifiers").toArray().contains("stale");
    check(stale && preserved.diagnosticCount() == 1, "syntax-error generation did not preserve stale facts");
    const GraphSnapshot fixed = good.preservingParseErrorsFrom(preserved);
    check(!fixed.node(fn, 0, 0, 1, true).value("modifiers").toArray().contains("stale"), "fixed facts remained stale");
    QJsonObject changedFn = node(fn, "function", "renamed", "main.py");
    const GraphSnapshot changed = GraphSnapshot::fromNdjson(
        ndjson({node(repo, "repository", "repo"), changedFn,
                node("n_000000000000000000000004", "variable", "x", "main.py")}, {}, {}, fn), RepositoryPath(temp.path()));
    const QJsonObject diff = changed.compareTo(good);
    check(diff.value("nodes").toObject().value("modified").toArray().size() == 1, "modified node not detected");
    check(diff.value("nodes").toObject().value("added").toArray().size() == 1, "added node not detected");
}

void liveUpdateRunnerTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"), "def main():\n    return 1\n\nif __name__ == '__main__':\n    main()\n");
    const LocalConfig config = LocalConfig::load(temp.path());
    const auto trace = QSharedPointer<TraceLog>::create(temp.path());
    HostState state(config, trace);
    AnalyzerRunner runner(&state, {"python3", QDir(CODE_VIEW_SOURCE_ROOT).filePath("services/python-analyzer/analyzer.py")});
    check(runner.runBlocking() && state.view().generation == 1, "initial live generation failed");
    writeFile(temp.filePath("main.py"), "def main(:\n    return 1\n");
    check(runner.runBlocking() && state.view().generation == 2 && state.view().graph->diagnosticCount() == 1,
          "syntax-error generation was not promoted");
    const QJsonArray staleMatches = state.view().graph->search("main", 100);
    bool stale = false;
    for (const QJsonValue& value: staleMatches)
        stale |= value.toObject().value("modifiers").toArray().contains("stale");
    check(stale, "live syntax error dropped last-good symbol facts");
    writeFile(temp.filePath("main.py"), "def main():\n    return 2\n\nif __name__ == '__main__':\n    main()\n");
    check(runner.runBlocking() && state.view().generation == 3 && state.view().graph->diagnosticCount() == 0,
          "fixed generation did not replace syntax diagnostic");
    const QJsonArray fixedMatches = state.view().graph->search("main", 100);
    for (const QJsonValue& value: fixedMatches)
        check(!value.toObject().value("modifiers").toArray().contains("stale"), "fixed live fact remained stale");
    writeFile(temp.filePath("code-view.json"), R"({"unknown":true})");
    check(!runner.runBlocking() && state.view().generation == 3 && state.view().indexState == "ready" &&
          !state.view().lastError.isEmpty(), "failed analyzer/config did not retain last-good generation");

    QFile::remove(temp.filePath("code-view.json"));
    const QString analyzer = QDir(CODE_VIEW_SOURCE_ROOT).filePath("services/python-analyzer/analyzer.py");
    const QString wrapper = temp.filePath("timeout-analyzer.py");
    writeFile(wrapper,
        "import os, pathlib, sys, time\n"
        "analyzer, root = sys.argv[1], pathlib.Path(sys.argv[2])\n"
        "if (root / 'analyzer-mode').read_text() == 'hang':\n    time.sleep(30)\n"
        "os.execv(sys.executable, [sys.executable, analyzer, *sys.argv[2:]])\n");
    writeFile(temp.filePath("analyzer-mode"), "healthy");
    HostState timeoutState(LocalConfig::load(temp.path()), trace);
    // The timeout belongs to the hung worker, not a race against healthy Python startup.
    AnalyzerRunner bounded(&timeoutState, {"python3", wrapper, analyzer}, nullptr, 2'000);
    check(bounded.runBlocking(3'000) && timeoutState.view().generation == 1, "bounded analyzer initial run failed");
    writeFile(temp.filePath("analyzer-mode"), "hang");
    check(!runAsync(bounded) && timeoutState.view().generation == 1 && timeoutState.view().indexState == "ready" &&
          timeoutState.view().lastError.contains("timed out"), "async timeout did not preserve last-good generation");
    writeFile(temp.filePath("analyzer-mode"), "healthy");
    check(runAsync(bounded) && timeoutState.view().generation == 2 && timeoutState.view().lastError.isEmpty(),
          "async analyzer did not recover after timeout");
}

void configAndPathTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"), "print('ok')\n");
    writeFile(temp.filePath("code-view.json"), R"({"schemaVersion":1,"entry":{"file":"main.py","symbol":"main","command":{"argv":["python3","main.py"],"mode":"manual"}},"languages":["python"],"index":{"tests":"include","modules":"show","externalPackages":"collapse","exclude":["generated/**"]},"canvas":{"initialView":"whole-repo","relationships":{"calls":true}},"git":{"base":"HEAD"}})");
    const LocalConfig config = LocalConfig::load(temp.path());
    check(config.startManual && config.startArgv == QStringList({"python3", "main.py"}), "manual argv not normalized");
    check(config.startDisplay == "python3 main.py" && config.startMode == "manual", "command display/mode missing");
    check(config.includeTests && config.isExcluded("generated/client.py"), "index exclusions not applied to watcher");
    const QJsonObject publicConfig = config.publicJson();
    check(publicConfig.value("modules") == "show" && publicConfig.value("initialView") == "whole-repo" &&
          publicConfig.value("relationships").toArray().contains("calls") && publicConfig.value("gitBase") == "HEAD",
          "canvas configuration was not exposed to the UI");
    writeFile(temp.filePath("code-view.json"), R"({"startCommand":"python -m pkg.main"})");
    const LocalConfig concise = LocalConfig::load(temp.path());
    check(!concise.startManual && concise.startArgv == QStringList({"python", "-m", "pkg.main"}),
          "document command was not normalized");
    writeFile(temp.filePath("code-view.json"), R"({"languages":["java"]})");
    throws([&]() { LocalConfig::load(temp.path()); }, "unsupported languages accepted");
    writeFile(temp.filePath("code-view.json"), R"({"wat":true})");
    throws([&]() { LocalConfig::load(temp.path()); }, "unknown config key accepted");
    writeFile(temp.filePath("code-view.json"), R"({"schemaVersion":"1"})");
    throws([&]() { LocalConfig::load(temp.path()); }, "non-numeric schema version accepted");
    writeFile(temp.filePath("code-view.json"), R"({"entry":{"file":"main.py","command":{"argv":["python3","main.py"],"mode":"manual"}},"startCommand":{"argv":["ignored"],"mode":"manual","unknown":true}})");
    throws([&]() { LocalConfig::load(temp.path()); }, "ignored malformed startCommand object accepted");
    for (const QByteArray& invalid: {QByteArray(R"({"entry":null})"), QByteArray(R"({"index":{"tests":[]}})"),
                                   QByteArray(R"({"index":{"modules":true}})"), QByteArray(R"({"index":{"externalPackages":{}}})"),
                                   QByteArray(R"({"canvas":{"initialView":[]}})"), QByteArray(R"({"canvas":{"initialView":null}})")})
    {
        writeFile(temp.filePath("code-view.json"), invalid);
        throws([&]() { LocalConfig::load(temp.path()); }, "wrong-typed config value used a default");
    }
    RepositoryPath paths(temp.path());
    check(paths.readFile("main.py", 100) == "print('ok')\n", "contained file bytes changed");
    throws([&]() { paths.readFile("main.py", 2); }, "bounded read accepted oversized file");
    throws([&]() { paths.resolve("../outside"); }, "path traversal accepted");
    const QByteArray outside = QFile::encodeName("/etc/passwd");
    const QByteArray link = QFile::encodeName(temp.filePath("escape"));
    if (::symlink(outside.constData(), link.constData()) == 0)
    {
        throws([&]() { paths.resolve("escape"); }, "symlink escape accepted");
        throws([&]() { paths.readFile("escape", 100); }, "descriptor read followed a symlink");
    }
    check(::mkfifo(QFile::encodeName(temp.filePath("pipe")).constData(), 0600) == 0, "FIFO fixture failed");
    throws([&]() { paths.readFile("pipe", 100); }, "descriptor read accepted a FIFO");

    QTemporaryDir pinned;
    QDir(pinned.path()).mkpath("root");
    QDir(pinned.path()).mkpath("outside");
    writeFile(pinned.filePath("root/main.py"), "safe");
    writeFile(pinned.filePath("outside/main.py"), "outside");
    RepositoryPath pinnedPaths(pinned.filePath("root"));
    check(QDir(pinned.path()).rename("root", "original"), "root rename fixture failed");
    check(::symlink(QFile::encodeName(pinned.filePath("outside")).constData(),
                    QFile::encodeName(pinned.filePath("root")).constData()) == 0, "root symlink fixture failed");
    check(pinnedPaths.readFile("main.py", 100) == "safe", "descriptor read re-anchored a replaced root");

    QTemporaryDir outsideConfig;
    writeFile(outsideConfig.filePath("code-view.json"), "{}");
    QFile::remove(temp.filePath("code-view.json"));
    const QByteArray configTarget = QFile::encodeName(outsideConfig.filePath("code-view.json"));
    const QByteArray configLink = QFile::encodeName(temp.filePath("code-view.json"));
    if (::symlink(configTarget.constData(), configLink.constData()) == 0)
        throws([&]() { LocalConfig::load(temp.path()); }, "config symlink escape accepted");
}

void analyzerCleanupTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"), "pass\n");
    const QString wrapper = temp.filePath("analyzer-wrapper.py");
    writeFile(wrapper,
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen(['/bin/sleep', '30'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "time.sleep(30)\n");
    const LocalConfig config = LocalConfig::load(temp.path());
    HostState state(config, QSharedPointer<TraceLog>::create(temp.path()));
    const QString blockingPid = temp.filePath("blocking.pid");
    AnalyzerRunner blocking(&state, {"python3", wrapper, blockingPid});
    check(!blocking.runBlocking(300), "blocking analyzer did not time out");
    check(processGone(waitForPid(blockingPid)), "blocking timeout left analyzer descendant alive");
    const QString asyncPid = temp.filePath("async.pid");
    AnalyzerRunner asynchronous(&state, {"python3", wrapper, asyncPid}, nullptr, 300);
    check(!runAsync(asynchronous), "async analyzer did not time out");
    check(processGone(waitForPid(asyncPid)), "async timeout left analyzer descendant alive");
    const QString destructorPid = temp.filePath("destructor.pid");
    qint64 child = 0;
    {
        AnalyzerRunner cleanup(&state, {"python3", wrapper, destructorPid});
        cleanup.requestReindex();
        child = waitForPid(destructorPid);
    }
    check(processGone(child), "analyzer destruction left descendant alive");
}

void startupFailureTests()
{
    QTemporaryDir repo;
    writeFile(repo.filePath("main.py"), "print('ok')\n");
    QProcess failedAnalyzer;
    failedAnalyzer.start(CODE_VIEW_SERVER_PATH,
        {"--repo", repo.path(), "--analyzer-arg", "/usr/bin/false", "--no-watch"});
    check(failedAnalyzer.waitForFinished(5'000) && failedAnalyzer.exitCode() == 2 &&
          failedAnalyzer.readAllStandardOutput().isEmpty(), "server listened after initial analyzer failure");

    QTemporaryDir hostileCwd;
    QDir(hostileCwd.path()).mkpath("python-analyzer");
    const QString hostileAnalyzer = hostileCwd.filePath("python-analyzer/analyzer.py");
    const QString marker = hostileCwd.filePath("executed");
    writeFile(hostileAnalyzer, QString("from pathlib import Path\nPath(%1).write_text('bad')\n")
                                   .arg(QString("'%1'").arg(marker)).toUtf8());
    QProcess trustedDiscovery;
    trustedDiscovery.setWorkingDirectory(hostileCwd.path());
    trustedDiscovery.start(CODE_VIEW_SERVER_PATH, {"--repo", repo.path(), "--no-watch"});
    check(trustedDiscovery.waitForStarted(5'000) && trustedDiscovery.waitForReadyRead(5'000) &&
          trustedDiscovery.readAllStandardOutput().contains("\"generation\":1") && !QFileInfo::exists(marker),
          "server did not use the analyzer beside its trusted executable");
    trustedDiscovery.terminate();
    if (!trustedDiscovery.waitForFinished(2'000))
    {
        trustedDiscovery.kill();
        trustedDiscovery.waitForFinished(2'000);
    }
}

void failedReindexApprovalTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"), "pass\n");
    writeFile(temp.filePath("code-view.json"),
              R"({"entry":"main.py","startCommand":{"argv":["/usr/bin/touch","APPROVED_START"],"mode":"manual"}})");
    const LocalConfig config = LocalConfig::load(temp.path());
    const GraphSnapshot graph = GraphSnapshot::fromNdjson(
        ndjson({node("n_000000000000000000000001", "file", "main.py", "main.py")}), RepositoryPath(temp.path()));
    HostState state(config, QSharedPointer<TraceLog>::create(temp.path()));
    state.indexingSucceeded(config, graph, 0);
    const StateView original = state.view();
    StartCommandController controller(&state, true);
    controller.approve(original.generation);

    state.indexingStarted();
    state.indexingFailed("invalid replacement configuration", 0);
    check(state.view().generation == original.generation && state.view().graph == original.graph &&
              state.view().indexState == "ready" && !state.view().lastError.isEmpty(),
          "failed reindex lost usable graph or error evidence");
    check(!controller.launch().value("approved").toBool() && !controller.launch().value("allowLaunch").toBool(),
          "failed reindex retained approval or launch permission");
    throws([&]() { controller.start(); }, "failed reindex started the previously approved command");
    throws([&]() { controller.approve(original.generation); }, "failed reindex accepted a fresh approval");
    check(!QFileInfo::exists(temp.filePath("APPROVED_START")), "failed reindex executed a command");

    state.indexingStarted();
    state.indexingSucceeded(config, graph, 0);
    const quint64 repairedGeneration = state.view().generation;
    check(repairedGeneration > original.generation && state.view().lastError.isEmpty() &&
              controller.launch().value("allowLaunch").toBool(),
          "repaired index did not restore launch availability");
    throws([&]() { controller.start(); }, "repaired index reused the pre-failure approval");
    throws([&]() { controller.approve(original.generation); }, "repaired index accepted an old displayed generation");
    controller.approve(repairedGeneration);
    check(controller.start().value("started").toBool(), "repaired index rejected a newly reviewed command");
    for (int attempt = 0; attempt < 100 && !QFileInfo::exists(temp.filePath("APPROVED_START")); ++attempt)
        QThread::msleep(5);
    check(QFileInfo::exists(temp.filePath("APPROVED_START")), "newly approved command did not execute after repair");
    controller.stop();

    controller.approve(repairedGeneration);
    state.indexingStarted();
    check(!controller.launch().value("approved").toBool() && !controller.launch().value("allowLaunch").toBool(),
          "indexing retained approval or launch permission");
    throws([&]() { controller.start(); }, "command started while indexing");
    throws([&]() { controller.approve(repairedGeneration); }, "approval accepted while indexing");
}

void commandAndEditorTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"),
              "import pathlib, subprocess, time\n"
              "child = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True)\n"
              "pathlib.Path('grandchild.pid').write_text(str(child.pid))\n"
              "time.sleep(30)\n");
    writeFile(temp.filePath("code-view.json"), R"({"entry":{"file":"main.py","command":{"argv":["python3","main.py"],"mode":"manual"}}})");
    const LocalConfig config = LocalConfig::load(temp.path());
    const auto trace = QSharedPointer<TraceLog>::create(temp.path());
    HostState state(config, trace);
    state.indexingSucceeded(config, analyze(temp.path()), 1);
    StartCommandController controller(&state, true);
    controller.approve(state.view().generation);
    const QJsonObject started = controller.start();
    const qint64 pid = started.value("pid").toInteger();
    for (int attempt = 0; attempt < 100 && !QFileInfo::exists(temp.filePath("grandchild.pid")); ++attempt)
        QThread::msleep(10);
    QFile grandchildFile(temp.filePath("grandchild.pid"));
    check(grandchildFile.open(QIODevice::ReadOnly), "start command did not launch descendant");
    const qint64 grandchildPid = grandchildFile.readAll().trimmed().toLongLong();
    check(started.value("running").toBool() && started.value("mode").toString() == "manual",
          "start response was not a full launch configuration");
    check(controller.status().value("running").toBool() && !controller.status().value("approved").toBool(),
          "start did not consume approval");
    const QJsonObject stopped = controller.stop();
    check(!stopped.value("running").toBool() && stopped.value("mode").toString() == "manual",
          "stop response was not a full launch configuration");
    check(processGone(pid), "stop left direct child alive");
    check(processGone(grandchildPid), "stop left command descendant alive");
    throws([&]() { controller.start(); }, "start reused consumed approval");

    writeFile(temp.filePath("orphan.py"),
              "import pathlib, subprocess\n"
              "child = subprocess.Popen(['/bin/sleep', '30'])\n"
              "pathlib.Path('orphan.pid').write_text(str(child.pid))\n");
    writeFile(temp.filePath("code-view.json"), R"({"entry":{"file":"orphan.py","command":{"argv":["python3","orphan.py"],"mode":"manual"}}})");
    const LocalConfig orphaned = LocalConfig::load(temp.path());
    state.indexingSucceeded(orphaned, analyze(temp.path()), 2);
    controller.approve(state.view().generation);
    controller.start();
    for (int attempt = 0; attempt < 100 && !QFileInfo::exists(temp.filePath("orphan.pid")); ++attempt)
        QThread::msleep(10);
    QFile orphanFile(temp.filePath("orphan.pid"));
    check(orphanFile.open(QIODevice::ReadOnly), "short-lived command did not launch descendant");
    const qint64 orphanPid = orphanFile.readAll().trimmed().toLongLong();
    for (int attempt = 0; attempt < 100 && controller.status().value("running").toBool(); ++attempt)
        QCoreApplication::processEvents(QEventLoop::AllEvents, 10);
    check(!controller.status().value("running").toBool(), "short-lived command leader did not exit");
    controller.stop();
    check(processGone(orphanPid), "leader exit left command descendant alive");

    const quint64 displayedGeneration = state.view().generation;
    controller.approve(displayedGeneration);
    writeFile(temp.filePath("code-view.json"), R"({"entry":{"file":"main.py","command":{"argv":["/usr/bin/false"],"mode":"manual"}}})");
    const LocalConfig changed = LocalConfig::load(temp.path());
    state.indexingSucceeded(changed, analyze(temp.path()), 3);
    check(!controller.status().value("approved").toBool(), "config change retained stale approval");
    throws([&]() { controller.start(); }, "changed command started with stale approval");
    throws([&]() { controller.approve(displayedGeneration); }, "unseen changed command accepted displayed approval");
    writeFile(temp.filePath("code-view.json"), R"({"entry":{"file":"main.py","command":{"argv":["python3","main.py"],"mode":"manual"}}})");
    const LocalConfig restored = LocalConfig::load(temp.path());
    state.indexingSucceeded(restored, analyze(temp.path()), 4);
    qint64 destructorPid = 0;
    qint64 destructorGrandchildPid = 0;
    QFile::remove(temp.filePath("grandchild.pid"));
    {
        StartCommandController cleanup(&state, true);
        cleanup.approve(state.view().generation);
        destructorPid = cleanup.start().value("pid").toInteger();
        for (int attempt = 0; attempt < 100 && !QFileInfo::exists(temp.filePath("grandchild.pid")); ++attempt)
            QThread::msleep(10);
        QFile childFile(temp.filePath("grandchild.pid"));
        check(childFile.open(QIODevice::ReadOnly), "destructor command did not launch descendant");
        destructorGrandchildPid = childFile.readAll().trimmed().toLongLong();
    }
    check(processGone(destructorPid), "controller destruction left direct child alive");
    check(processGone(destructorGrandchildPid), "controller destruction left descendant alive");

    const QString capture = temp.filePath("editor-args.txt");
    const QString script = temp.filePath("fake-editor");
    writeFile(script, QString("#!/bin/sh\nprintf '%s\\n' \"$@\" > '%1'\n").arg(capture).toUtf8());
    ::chmod(QFile::encodeName(script).constData(), 0700);
    const QByteArray before = hashFile(temp.filePath("main.py"));
    EditorLauncher("code", script).open(RepositoryPath(temp.path()), "main.py", 4, 2);
    for (int attempt = 0; attempt < 100 && !QFileInfo::exists(capture); ++attempt)
        QThread::msleep(10);
    QFile captured(capture);
    check(captured.open(QIODevice::ReadOnly), "fake editor did not capture argv");
    const QStringList args = QString::fromUtf8(captured.readAll()).split('\n', Qt::SkipEmptyParts);
    check(args == QStringList({"--goto", RepositoryPath(temp.path()).resolve("main.py") + ":4:2"}),
          "editor argv was not fixed and exact");
    check(hashFile(temp.filePath("main.py")) == before, "editor open changed source bytes");
    throws([&]() { EditorLauncher("none").open(RepositoryPath(temp.path()), "main.py", 1, 1); },
           "none editor did not reject open");
}

void gitComparisonTests()
{
    QTemporaryDir temp;
    run("git", {"init", "-q"}, temp.path());
    run("git", {"config", "user.email", "test@example.invalid"}, temp.path());
    run("git", {"config", "user.name", "Code View Test"}, temp.path());
    writeFile(temp.filePath("main.py"), "def main():\n    return 1\n");
    run("git", {"add", "main.py"}, temp.path());
    run("git", {"commit", "-qm", "base"}, temp.path());
    run("git", {"branch", "baseline"}, temp.path());
    const QString commit = QString::fromUtf8(run("git", {"rev-parse", "HEAD"}, temp.path())).trimmed();
    const auto trace = QSharedPointer<TraceLog>::create(temp.path());
    const QString analyzer = QDir(CODE_VIEW_SOURCE_ROOT).filePath("services/python-analyzer/analyzer.py");
    GraphComparator comparator(temp.path(), {"python3", analyzer}, trace);
    const GraphSnapshot clean = analyze(temp.path());
    const QJsonObject cleanComparison = comparator.compare("baseline", "WORKTREE", clean);
    const QJsonObject cleanNodes = cleanComparison.value("nodes").toObject().value("counts").toObject();
    const QJsonObject cleanEdges = cleanComparison.value("edges").toObject().value("counts").toObject();
    check(cleanNodes.value("added").toInt() == 0 && cleanNodes.value("removed").toInt() == 0 &&
          cleanNodes.value("modified").toInt() == 0 && cleanEdges.value("added").toInt() == 0 &&
          cleanEdges.value("removed").toInt() == 0 && cleanEdges.value("modified").toInt() == 0,
          "clean graph comparison invented changes");
    writeFile(temp.filePath("main.py"), "def main():\n    return 1\n\ndef added():\n    return 2\n");
    const GraphSnapshot current = analyze(temp.path());
    const QJsonObject comparison = comparator.compare("baseline", "WORKTREE", current);
    check(comparison.value("baseCommit").toString() == commit, "branch base did not resolve exactly");
    check(comparison.value("nodes").toObject().value("added").toArray().size() > 0, "graph compare missed added symbol");
    check(GitInspector(temp.path()).compare(commit).value("fileCount").toInt() == 1, "file compare missed worktree edit");
    check(GitInspector(temp.path()).unifiedDiff(commit, "main.py").contains("def added"), "node source diff was empty");
    const QJsonObject refs = GitInspector(temp.path()).refs();
    check(refs.value("hasHead").toBool() && refs.value("branches").toArray().size() >= 2, "Git refs missing branch/HEAD");
    check(GitInspector(temp.path()).revision(commit).value("commit").toString() == commit, "commit base revision failed");
    throws([&]() { GitInspector(temp.path()).revision("bad ref"); }, "invalid ref accepted");

    const QString hook = temp.filePath("fsmonitor-hook");
    const QString hookMarker = temp.filePath("fsmonitor-executed");
    writeFile(hook, QString("#!/bin/sh\n: > '%1'\nprintf '{}\n'\n").arg(hookMarker).toUtf8());
    ::chmod(QFile::encodeName(hook).constData(), 0700);
    run("git", {"config", "core.fsmonitor", hook}, temp.path());
    GitInspector(temp.path()).compare(commit);
    GitInspector(temp.path()).revision(commit);
    check(!QFileInfo::exists(hookMarker), "Git inspection executed repository fsmonitor");

    const QString fakeGit = temp.filePath("git");
    const QString fakeGitMarker = temp.filePath("fake-git-ran");
    writeFile(fakeGit, QString("#!/bin/sh\n: > '%1'\nexec /usr/bin/git \"$@\"\n").arg(fakeGitMarker).toUtf8());
    ::chmod(QFile::encodeName(fakeGit).constData(), 0700);
    const QByteArray originalPath = qgetenv("PATH");
    qputenv("PATH", QFile::encodeName(temp.path()) + ':' + originalPath);
    GitInspector(temp.path()).revision(commit);
    GraphComparator trustedComparator(temp.path(), {"python3", analyzer}, trace);
    trustedComparator.compare(commit, "WORKTREE", current);
    qputenv("PATH", originalPath);
    check(!QFileInfo::exists(fakeGitMarker), "Git lookup executed repository-controlled PATH entry");

    QTemporaryDir nested;
    run("git", {"init", "-q"}, nested.path());
    run("git", {"config", "user.email", "test@example.invalid"}, nested.path());
    run("git", {"config", "user.name", "Code View Test"}, nested.path());
    QDir(nested.path()).mkpath("sub");
    writeFile(nested.filePath("sub/main.py"), "def nested():\n    return 1\n");
    run("git", {"add", "sub/main.py"}, nested.path());
    run("git", {"commit", "-qm", "nested"}, nested.path());
    const QString nestedRoot = nested.filePath("sub");
    GraphComparator nestedComparator(nestedRoot, {"python3", analyzer}, QSharedPointer<TraceLog>::create(nestedRoot));
    const QJsonObject nestedComparison = nestedComparator.compare("HEAD", "WORKTREE", analyze(nestedRoot));
    check(nestedComparison.value("nodes").toObject().value("counts").toObject().value("modified").toInt() == 0,
          "nested worktree graph comparison failed");

    QTemporaryDir removed;
    run("git", {"init", "-q"}, removed.path());
    run("git", {"config", "user.email", "test@example.invalid"}, removed.path());
    run("git", {"config", "user.name", "Code View Test"}, removed.path());
    QDir(removed.path()).mkpath("pkg");
    writeFile(removed.filePath("pkg/main.py"), "def removed():\n    return 1\n");
    run("git", {"add", "pkg/main.py"}, removed.path());
    run("git", {"commit", "-qm", "removed-base"}, removed.path());
    QFile::remove(removed.filePath("pkg/main.py"));
    QDir(removed.path()).rmdir("pkg");
    check(GitInspector(removed.path()).unifiedDiff("HEAD", "pkg/main.py").contains("def removed"),
          "removed nested-file diff was unavailable");

    QTemporaryDir empty;
    run("git", {"init", "-q"}, empty.path());
    check(!GitInspector(empty.path()).refs().value("hasHead").toBool(), "unborn repository reported HEAD");
    throws([&]() { GitInspector(empty.path()).revision(); }, "unborn repository revision succeeded");
}

struct HttpResult { int status; QByteArray body; QList<QPair<QByteArray, QByteArray>> headers; };

HttpResult request(QNetworkAccessManager& manager, const QUrl& url,
                   const QList<QPair<QByteArray, QByteArray>>& headers = {}, const QByteArray& body = {})
{
    QNetworkRequest request(url);
    for (const auto& [name, value]: headers)
        request.setRawHeader(name, value);
    QNetworkReply* reply = body.isNull() ? manager.get(request) : manager.post(request, body);
    QEventLoop loop;
    QTimer timer;
    timer.setSingleShot(true);
    QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    QObject::connect(&timer, &QTimer::timeout, &loop, &QEventLoop::quit);
    timer.start(5'000);
    loop.exec();
    if (!timer.isActive())
        throw std::runtime_error("HTTP gate timed out");
    const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    HttpResult result{status, reply->readAll(), reply->rawHeaderPairs()};
    reply->deleteLater();
    return result;
}

QByteArray header(const HttpResult& result, const QByteArray& name)
{
    for (const auto& [key, value]: result.headers)
        if (key.compare(name, Qt::CaseInsensitive) == 0)
            return value;
    return {};
}

void httpSecurityTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"), "print('ok')\n");
    QDir(temp.path()).mkpath("pkg");
    writeFile(temp.filePath("pkg/nested.py"), "def nested():\n    return 1\n");
    const LocalConfig config = LocalConfig::load(temp.path());
    const auto trace = QSharedPointer<TraceLog>::create(temp.path());
    HostState state(config, trace);
    state.indexingSucceeded(config, analyze(temp.path()), 1);
    check(state.view().graph->containsFile("pkg/nested.py"), "nested source missing from active graph");
    AnalyzerRunner analyzer(&state, {"python3", QDir(CODE_VIEW_SOURCE_ROOT).filePath("services/python-analyzer/analyzer.py")});
    StartCommandController commands(&state, false);
    HttpHost host(&state, &analyzer, &commands, EditorLauncher("none"), {});
    check(host.listen(), host.listenError().toUtf8().constData());
    QNetworkAccessManager manager;
    const QByteArray bearer = "Bearer " + host.token().toLatin1();
    const HttpResult boot = request(manager, QUrl(host.launchUrl()));
    const QByteArray csp = header(boot, "Content-Security-Policy");
    check(boot.status == 200 && csp.contains("style-src 'self' 'unsafe-inline'") &&
          csp.contains("form-action 'none'"),
          "tokenized UI bootstrap or CSP failed");
    check(request(manager, QUrl(host.origin() + "/")).status == 200, "cleaned UI URL could not reload");
    check(request(manager, QUrl(host.origin() + "/?token=wrong")).status == 401,
          "UI bootstrap accepted an invalid supplied token");
    check(request(manager, QUrl(host.origin() + "/api/v1/status")).status == 401, "API accepted missing bearer");
    check(request(manager, QUrl(host.origin() + "/api/v1/status"), {{"Authorization", bearer}}).status == 200,
          "API rejected valid bearer without Origin");
    const HttpResult graph = request(manager, QUrl(host.origin() + "/api/v1/graph?view=repository&base=HEAD"),
                                     {{"Authorization", bearer}});
    const QJsonObject graphBody = QJsonDocument::fromJson(graph.body).object();
    check(graph.status == 200 && graphBody.value("generation").toInt() == 1 && graphBody.value("comparison").isNull() &&
          !graphBody.value("graph").toObject().value("nodes").toArray().isEmpty(),
          "non-Git graph did not remain available with null comparison");
    check(request(manager, QUrl(host.origin() + "/api/v1/status"),
                  {{"Authorization", bearer}, {"Origin", "https://hostile.invalid"}}).status == 403,
          "API accepted hostile Origin");
    check(request(manager, QUrl(host.origin() + "/api/v1/status"),
                  {{"Authorization", bearer}, {"Host", "hostile.invalid"}}).status == 403,
          "API accepted hostile Host");
    check(request(manager, QUrl(host.origin() + "/main.py"), {{"Authorization", bearer}}).status == 404,
          "static routing exposed repository source");
    check(request(manager, QUrl(host.origin() + "/missing/deep/path"), {{"Host", "hostile.invalid"}}).status == 403,
          "missing route bypassed Host guard");
    const HttpResult source = request(
        manager,
        QUrl(host.origin() + "/api/v1/source?path=pkg%2Fnested.py&startLine=1&endLine=2"),
        {{"Authorization", bearer}});
    const QByteArray sourceFailure = "encoded source path was not decoded: " + source.body;
    check(source.status == 200 && source.body.contains("def nested"), sourceFailure.constData());
    const QByteArray openBody = QJsonDocument(QJsonObject{{"path", "main.py"}, {"line", 1}, {"column", 1}})
                                      .toJson(QJsonDocument::Compact);
    check(request(manager, QUrl(host.origin() + "/api/v1/editor/open"),
                  {{"Authorization", bearer}, {"Content-Type", "application/json"}}, openBody).status == 409,
          "default none editor did not return conflict");
}

void hostShutdownTests()
{
    QTemporaryDir temp;
    writeFile(temp.filePath("main.py"),
        "import pathlib, subprocess, time\n"
        "child = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True)\n"
        "pathlib.Path('child.pid').write_text(str(child.pid))\n"
        "time.sleep(30)\n");
    writeFile(temp.filePath("code-view.json"),
        R"({"entry":{"file":"main.py","command":{"argv":["python3","main.py"],"mode":"manual"}}})");
    QNetworkAccessManager manager;
    for (const int signal: {SIGTERM, SIGINT})
    {
        QFile::remove(temp.filePath("child.pid"));
        QProcess server;
        server.start(CODE_VIEW_SERVER_PATH, {"--repo", temp.path(), "--no-watch", "--allow-command"});
        check(server.waitForStarted(5'000) && server.waitForReadyRead(5'000), "shutdown server did not start");
        const QJsonObject startup = QJsonDocument::fromJson(server.readAllStandardOutput().trimmed()).object();
        QUrl url(startup.value("url").toString());
        const QByteArray bearer = "Bearer " + QUrlQuery(url).queryItemValue("token").toLatin1();
        const QByteArray approval = QJsonDocument(QJsonObject{{"schemaVersion", "code-view.launch-approval/v2"},
                                                             {"generation", startup.value("generation")}})
                                        .toJson(QJsonDocument::Compact);
        url.setQuery(QString());
        url.setPath("/api/v1/launch/approve");
        check(request(manager, url, {{"Authorization", bearer}}, QByteArray("")).status == 409,
              "unbound approval request was accepted");
        check(request(manager, url, {{"Authorization", bearer}}, approval).status == 200,
              "shutdown test approval failed");
        url.setPath("/api/v1/launch/start");
        const HttpResult launch = request(manager, url, {{"Authorization", bearer}}, QByteArray(""));
        const qint64 leader = QJsonDocument::fromJson(launch.body).object().value("pid").toInteger();
        check(launch.status == 200 && leader > 0, "shutdown test command failed");
        const qint64 child = waitForPid(temp.filePath("child.pid"));
        ::kill(pid_t(server.processId()), signal);
        const bool stopped = server.waitForFinished(3'000);
        const bool clean = processGone(leader) && processGone(child);
        if (!stopped)
        {
            server.kill();
            server.waitForFinished(1'000);
        }
        if (!clean)
        {
            ::kill(pid_t(child), SIGKILL);
            ::kill(pid_t(leader), SIGKILL);
        }
        check(stopped && clean, "host signal left command leader or detached child alive");
    }
}
}

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    const bool gate = argc == 2 && QString::fromLocal8Bit(argv[1]) == "--gate";
    if (argc > 2 || (argc == 2 && !gate))
    {
        std::cerr << "usage: code-view-local-server-tests [--gate]\n";
        return 2;
    }
    try
    {
        QElapsedTimer timer;
        timer.start();
        graphContractTests();
        staleAndDiffTests();
        configAndPathTests();
        failedReindexApprovalTests();
        if (!gate)
        {
            liveUpdateRunnerTests();
            analyzerCleanupTests();
            startupFailureTests();
            commandAndEditorTests();
            gitComparisonTests();
            httpSecurityTests();
            hostShutdownTests();
        }
        std::cout << "PASS " << checks << " checks in " << timer.elapsed() << " ms\n";
        return 0;
    }
    catch (const std::exception& exception)
    {
        std::cerr << "FAIL after " << checks << " checks: " << exception.what() << '\n';
        return 1;
    }
}
