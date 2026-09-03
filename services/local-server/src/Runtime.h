#pragma once

#include "Config.h"
#include "Graph.h"

#include <QFileSystemWatcher>
#include <QCryptographicHash>
#include <QDir>
#include <QJsonObject>
#include <QMutex>
#include <QProcess>
#include <QReadWriteLock>
#include <QSharedPointer>
#include <QTimer>

void installShutdownSignals();
bool shutdownRequested();

class TraceLog
{
public:
    explicit TraceLog(const QString& repositoryRoot);
    const QString& path() const { return m_path; }
    void write(const QString& event, QJsonObject fields = {});

private:
    QString m_path;
    QMutex m_mutex;
};

struct StateView
{
    LocalConfig config;
    QSharedPointer<const GraphSnapshot> graph;
    QString indexState;
    QString lastError;
    quint64 generation = 0;
};

class HostState
{
public:
    HostState(LocalConfig config, QSharedPointer<TraceLog> trace);

    StateView view() const;
    void indexingStarted();
    void indexingSucceeded(LocalConfig config, GraphSnapshot graph, qint64 elapsedMs);
    void indexingFailed(const QString& message, qint64 elapsedMs);
    QJsonObject statusJson() const;
    QSharedPointer<TraceLog> trace() const { return m_trace; }

private:
    mutable QReadWriteLock m_lock;
    LocalConfig m_config;
    QSharedPointer<const GraphSnapshot> m_graph;
    QString m_indexState = "empty";
    QString m_lastError;
    quint64 m_generation = 0;
    QSharedPointer<TraceLog> m_trace;
};

class AnalyzerRunner: public QObject
{
    Q_OBJECT

public:
    AnalyzerRunner(HostState* state, QStringList analyzerArgv, QObject* parent = nullptr, int timeoutMs = 120'000);
    ~AnalyzerRunner() override;
    void requestReindex();
    bool runBlocking(int timeoutMs = 120'000);
    const QStringList& analyzerArgv() const { return m_analyzerArgv; }

signals:
    void generationFinished(bool success);

private:
    void startAsync();
    bool acceptResult(int exitCode, QProcess::ExitStatus status, const QByteArray& output, const QByteArray& errorOutput, qint64 elapsedMs);

    HostState* m_state;
    QStringList m_analyzerArgv;
    QProcess* m_process = nullptr;
    qint64 m_processGroupId = 0;
    QByteArray m_output;
    QByteArray m_errorOutput;
    bool m_dirty = false;
    qint64 m_startedMs = 0;
    int m_timeoutMs;
    QTimer m_timeout;
};

class GitInspector
{
public:
    explicit GitInspector(QString repositoryRoot): m_root(std::move(repositoryRoot)) {}

    QJsonObject revision(const QString& ref = "HEAD") const;
    QJsonObject compare(const QString& base, const QString& target = "WORKTREE") const;
    QJsonObject refs() const;
    QString resolveCommit(const QString& ref) const;
    QString unifiedDiff(const QString& base, const QString& relativePath) const;

private:
    QByteArray run(const QStringList& arguments, bool allowFailure = false) const;
    QString m_root;
};

class GraphComparator
{
public:
    GraphComparator(QString repositoryRoot, QStringList analyzerArgv, QSharedPointer<TraceLog> trace);
    QJsonObject compare(const QString& base, const QString& target, const GraphSnapshot& current);

private:
    QSharedPointer<const GraphSnapshot> snapshot(const QString& commit);
    QString m_root;
    QStringList m_analyzerArgv;
    QSharedPointer<TraceLog> m_trace;
    QHash<QString, QSharedPointer<const GraphSnapshot>> m_cache;
};

class StartCommandController: public QObject
{
    Q_OBJECT

public:
    StartCommandController(HostState* state, bool allowedByCli, QObject* parent = nullptr);
    ~StartCommandController() override;
    QJsonObject approve(quint64 displayedGeneration);
    QJsonObject start();
    QJsonObject launch() const;
    QJsonObject status() const;
    QJsonObject stop();

private:
    bool approvalMatches(const StateView& current) const;
    bool stopProcessGroup(bool strict);
    HostState* m_state;
    bool m_allowedByCli;
    bool m_sessionApproved = false;
    quint64 m_approvedGeneration = 0;
    QStringList m_approvedArgv;
    QString m_approvedRoot;
    QProcess* m_process = nullptr;
    qint64 m_processGroupId = 0;
    bool m_started = false;
    int m_lastExitCode = 0;
    QProcess::ExitStatus m_lastExitStatus = QProcess::NormalExit;
};

class EditorLauncher
{
public:
    EditorLauncher(QString adapter, QString programOverride = {});
    QJsonObject open(const RepositoryPath& paths, const QString& relativePath, int line, int column) const;

private:
    QString m_adapter;
    QString m_programOverride;
};

class RepositoryWatcher: public QObject
{
    Q_OBJECT

public:
    RepositoryWatcher(HostState* state, AnalyzerRunner* runner, QObject* parent = nullptr);
    QByteArray scanNowForTests() const;

private:
    void scan();
    void schedule();
    void addDirectory(const QDir& directory, const LocalConfig& config, QCryptographicHash& hash) const;

    HostState* m_state;
    AnalyzerRunner* m_runner;
    QFileSystemWatcher m_watcher;
    QTimer m_poll;
    QTimer m_debounce;
    QByteArray m_manifest;
};
