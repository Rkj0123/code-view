#include "HttpHost.h"

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QTextStream>

namespace
{
QString firstExisting(const QStringList& candidates, bool directory)
{
    for (const QString& candidate: candidates)
    {
        const QFileInfo info(candidate);
        if ((directory && info.isDir()) || (!directory && info.isFile()))
            return info.canonicalFilePath();
    }
    return {};
}

QString defaultAnalyzer()
{
    const QString app = QCoreApplication::applicationDirPath();
    return firstExisting({QDir(app).filePath("../../python-analyzer/analyzer.py"),
                          QDir(app).filePath("../python-analyzer/analyzer.py"),
                          QDir(app).filePath("../share/code-view/python-analyzer/analyzer.py")}, false);
}

QString defaultWebRoot()
{
    const QString app = QCoreApplication::applicationDirPath();
    return firstExisting({QDir(app).filePath("../../web-canvas/dist"),
                          QDir(app).filePath("../web-canvas/dist"),
                          QDir(app).filePath("../share/code-view/web-canvas")}, true);
}
}

int main(int argc, char* argv[])
{
    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName("code-view-local-server");

    QCommandLineParser parser;
    parser.setApplicationDescription("Loopback-only Code View host");
    parser.addHelpOption();
    parser.addVersionOption();
    parser.addOption({"repo", "Repository root", "path", QDir::currentPath()});
    parser.addOption({"port", "Loopback port; 0 chooses an ephemeral port", "port", "0"});
    parser.addOption({"web-root", "Built web-canvas directory", "path"});
    parser.addOption({"analyzer-arg", "Analyzer argv item; repeat in order", "value"});
    parser.addOption({"allow-command", "Permit separately approved manual start commands"});
    parser.addOption({"editor", "Fixed editor adapter: none, code, cursor, zed, sublime", "adapter", "none"});
    parser.addOption({"no-watch", "Disable repository watching"});
    parser.process(application);

    try
    {
        installShutdownSignals();
        QTimer shutdownPoll;
        QObject::connect(&shutdownPoll, &QTimer::timeout, &application, [&application]() {
            if (shutdownRequested())
                application.quit();
        });
        shutdownPoll.start(50);
        const LocalConfig config = LocalConfig::load(parser.value("repo"));
        QStringList analyzerArgv = parser.values("analyzer-arg");
        if (analyzerArgv.isEmpty())
        {
            const QString analyzerPath = defaultAnalyzer();
            if (analyzerPath.isEmpty())
                throw std::runtime_error("cannot find services/python-analyzer/analyzer.py; pass repeated --analyzer-arg values");
            const QString python = firstExisting({"/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"}, false);
            if (python.isEmpty())
                throw std::runtime_error("cannot find Python 3; pass repeated --analyzer-arg values");
            analyzerArgv = {python, analyzerPath};
        }
        const auto trace = QSharedPointer<TraceLog>::create(config.root);
        HostState state(config, trace);
        AnalyzerRunner analyzer(&state, analyzerArgv);
        if (!analyzer.runBlocking())
            throw std::runtime_error("initial repository analysis failed");
        StartCommandController commands(&state, parser.isSet("allow-command"));
        const QString webRoot = parser.isSet("web-root") ? parser.value("web-root") : defaultWebRoot();
        HttpHost host(&state, &analyzer, &commands, EditorLauncher(parser.value("editor")), webRoot);
        bool portOk = false;
        const int port = parser.value("port").toInt(&portOk);
        if (!portOk || port < 0 || port > 65535)
            throw std::runtime_error("port must be between 0 and 65535");
        if (!host.listen(quint16(port)))
            throw std::runtime_error(("failed to bind loopback HTTP server: " + host.listenError()).toStdString());
        QJsonObject startup{{"url", host.launchUrl()}, {"tracePath", trace->path()}, {"generation", qint64(state.view().generation)}};
        QTextStream(stdout) << QJsonDocument(startup).toJson(QJsonDocument::Compact) << Qt::endl;
        std::unique_ptr<RepositoryWatcher> watcher;
        if (!parser.isSet("no-watch"))
            watcher = std::make_unique<RepositoryWatcher>(&state, &analyzer);
        return application.exec();
    }
    catch (const std::exception& exception)
    {
        QTextStream(stderr) << "code-view-local-server: " << exception.what() << Qt::endl;
        return 2;
    }
}
