#include "Runtime.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMutexLocker>
#include <QRegularExpression>
#include <QTemporaryDir>
#include <QProcessEnvironment>

#include <cerrno>
#include <csignal>
#include <stdexcept>
#include <utility>
#include <vector>
#include <unistd.h>
#ifdef Q_OS_MACOS
#include <libproc.h>
#endif

namespace
{
constexpr qsizetype MaxAnalyzerOutput = 100 * 1024 * 1024;
constexpr qsizetype MaxProcessOutput = 2 * 1024 * 1024;
constexpr auto TrustedGit = "/usr/bin/git";
volatile sig_atomic_t shutdownSignal = 0;

void receiveShutdownSignal(int signal)
{
    shutdownSignal = signal;
}

std::runtime_error error(const QString& message)
{
    return std::runtime_error(message.toStdString());
}

qint64 nowMs()
{
    return QDateTime::currentMSecsSinceEpoch();
}

QString safeError(const QByteArray& bytes)
{
    return QString::fromUtf8(bytes.left(16 * 1024)).trimmed();
}

bool launchableIndex(const StateView& current)
{
    return current.generation && current.indexState == "ready" && current.lastError.isEmpty();
}

void ownProcessGroup(QProcess& process)
{
    process.setChildProcessModifier([]() {
        if (::setsid() == -1)
            ::_exit(127);
    });
}

bool waitForProcess(QProcess& process, int timeoutMs)
{
    QElapsedTimer timer;
    timer.start();
    while (!shutdownRequested() && timer.elapsed() < timeoutMs)
        if (process.waitForFinished(qMax(1, qMin(50, timeoutMs - int(timer.elapsed())))))
            return true;
    return process.state() == QProcess::NotRunning;
}

bool stopOwnedProcess(QProcess& process, qint64 groupId, bool strict = false)
{
    if (groupId <= 0)
        return false;
#ifdef Q_OS_MACOS
    QList<proc_bsdinfo> descendants;
    QList<pid_t> parents{pid_t(groupId)};
    QSet<pid_t> seen{pid_t(groupId)};
    for (qsizetype index = 0; index < parents.size(); ++index)
    {
        std::vector<pid_t> children(32);
        int count;
        for (;;)
        {
            count = ::proc_listchildpids(parents[index], children.data(), int(children.size() * sizeof(pid_t)));
            if (count < int(children.size()))
                break;
            children.resize(children.size() * 2);
        }
        for (int child = 0; child < count; ++child)
        {
            proc_bsdinfo info {};
            const pid_t pid = children[child];
            if (pid > 0 && !seen.contains(pid) &&
                ::proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) == int(sizeof(info)) &&
                info.pbi_ppid == uint32_t(parents[index]))
            {
                seen.insert(pid);
                parents.append(pid);
                descendants.append(info);
            }
        }
    }
    auto signalDescendants = [&](int signal) {
        for (auto it = descendants.crbegin(); it != descendants.crend(); ++it)
        {
            proc_bsdinfo current {};
            if (::proc_pidinfo(int(it->pbi_pid), PROC_PIDTBSDINFO, 0, &current, sizeof(current)) == int(sizeof(current)) &&
                current.pbi_start_tvsec == it->pbi_start_tvsec && current.pbi_start_tvusec == it->pbi_start_tvusec)
                ::kill(pid_t(it->pbi_pid), signal);
        }
    };
    // ponytail: detached descendants reparented before this snapshot need their own supervisor.
    signalDescendants(SIGTERM);
#endif
    if (::kill(-pid_t(groupId), SIGTERM) != 0 && errno != ESRCH && strict)
        throw error("owned process group could not be terminated");
    if (process.state() != QProcess::NotRunning)
        process.waitForFinished(250);
#ifdef Q_OS_MACOS
    signalDescendants(SIGKILL);
#endif
    const bool killed = ::kill(-pid_t(groupId), 0) == 0 || errno == EPERM;
    if (killed && ::kill(-pid_t(groupId), SIGKILL) != 0 && errno != ESRCH && strict)
        throw error("owned process group could not be killed");
    if (process.state() != QProcess::NotRunning)
        process.waitForFinished(1'000);
    return killed;
}

void hardenGit(QProcess& process, QStringList& arguments)
{
    QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
    for (const QString& name: environment.keys())
        if (name.startsWith("GIT_CONFIG_") || name == "GIT_DIR" || name == "GIT_WORK_TREE" ||
            name == "GIT_COMMON_DIR" || name == "GIT_OBJECT_DIRECTORY" ||
            name == "GIT_ALTERNATE_OBJECT_DIRECTORIES")
            environment.remove(name);
    environment.insert("GIT_CONFIG_GLOBAL", "/dev/null");
    environment.insert("GIT_CONFIG_NOSYSTEM", "1");
    environment.insert("GIT_ATTR_NOSYSTEM", "1");
    environment.insert("GIT_OPTIONAL_LOCKS", "0");
    environment.insert("GIT_PAGER", "cat");
    environment.insert("GIT_TERMINAL_PROMPT", "0");
    environment.insert("GIT_SSH_COMMAND", "/usr/bin/false");
    process.setProcessEnvironment(environment);
    arguments.prepend("core.pager=cat");
    arguments.prepend("-c");
    arguments.prepend("diff.external=");
    arguments.prepend("-c");
    arguments.prepend("core.hooksPath=/dev/null");
    arguments.prepend("-c");
    arguments.prepend("core.fsmonitor=false");
    arguments.prepend("-c");
}
}

void installShutdownSignals()
{
    struct sigaction action {};
    action.sa_handler = receiveShutdownSignal;
    sigemptyset(&action.sa_mask);
    if (::sigaction(SIGINT, &action, nullptr) != 0 || ::sigaction(SIGTERM, &action, nullptr) != 0)
        throw error("cannot install shutdown signal handlers");
}

bool shutdownRequested()
{
    return shutdownSignal != 0;
}

TraceLog::TraceLog(const QString& repositoryRoot)
{
    const QByteArray digest = QCryptographicHash::hash(repositoryRoot.toUtf8(), QCryptographicHash::Sha256).toHex().left(16);
    const QString directory = QDir::temp().filePath("code-view-local-server/" + QString::fromLatin1(digest));
    if (!QDir().mkpath(directory))
        throw error("cannot create trace directory");
    m_path = QDir(directory).filePath("trace.jsonl");
}

void TraceLog::write(const QString& event, QJsonObject fields)
{
    fields.insert("event", event);
    fields.insert("timestamp", QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
    QMutexLocker lock(&m_mutex);
    QFile file(m_path);
    if (file.open(QIODevice::WriteOnly | QIODevice::Append))
    {
        file.write(QJsonDocument(fields).toJson(QJsonDocument::Compact));
        file.write("\n");
    }
}

HostState::HostState(LocalConfig config, QSharedPointer<TraceLog> trace)
    : m_config(std::move(config)), m_graph(QSharedPointer<GraphSnapshot>::create()), m_trace(std::move(trace))
{
}

StateView HostState::view() const
{
    QReadLocker lock(&m_lock);
    return {m_config, m_graph, m_indexState, m_lastError, m_generation};
}

void HostState::indexingStarted()
{
    QWriteLocker lock(&m_lock);
    m_indexState = "indexing";
    m_lastError.clear();
    m_trace->write("index.started", {{"generation", qint64(m_generation + 1)}});
}

void HostState::indexingSucceeded(LocalConfig config, GraphSnapshot graph, qint64 elapsedMs)
{
    QWriteLocker lock(&m_lock);
    m_config = std::move(config);
    m_graph = QSharedPointer<GraphSnapshot>::create(std::move(graph));
    ++m_generation;
    m_indexState = "ready";
    m_lastError.clear();
    m_trace->write("index.succeeded", {
        {"generation", qint64(m_generation)},
        {"durationMs", elapsedMs},
        {"nodes", m_graph->nodeCount()},
        {"edges", m_graph->edgeCount()},
        {"diagnostics", m_graph->diagnosticCount()},
    });
}

void HostState::indexingFailed(const QString& message, qint64 elapsedMs)
{
    QWriteLocker lock(&m_lock);
    m_indexState = m_generation ? "ready" : "error";
    m_lastError = message;
    m_trace->write("index.failed", {
        {"generationPreserved", qint64(m_generation)},
        {"durationMs", elapsedMs},
        {"error", message.left(16 * 1024)},
    });
}

QJsonObject HostState::statusJson() const
{
    const StateView current = view();
    return {
        {"state", current.indexState},
        {"generation", qint64(current.generation)},
        {"lastError", current.lastError.isEmpty() ? QJsonValue() : QJsonValue(current.lastError)},
        {"nodeCount", current.graph->nodeCount()},
        {"edgeCount", current.graph->edgeCount()},
        {"diagnosticCount", current.graph->diagnosticCount()},
        {"tracePath", m_trace->path()},
    };
}

AnalyzerRunner::AnalyzerRunner(HostState* state, QStringList analyzerArgv, QObject* parent, int timeoutMs)
    : QObject(parent), m_state(state), m_analyzerArgv(std::move(analyzerArgv)), m_timeoutMs(timeoutMs)
{
    if (m_analyzerArgv.isEmpty())
        throw error("analyzer argv must not be empty");
    if (m_timeoutMs <= 0)
        throw error("analyzer timeout must be positive");
    m_timeout.setSingleShot(true);
    connect(&m_timeout, &QTimer::timeout, this, [this]() {
        if (m_process && m_process->state() != QProcess::NotRunning)
        {
            m_errorOutput += "analyzer timed out";
            stopOwnedProcess(*m_process, m_process->processId());
        }
    });
}

AnalyzerRunner::~AnalyzerRunner()
{
    m_timeout.stop();
    m_dirty = false;
    if (m_process && m_process->state() != QProcess::NotRunning)
    {
        disconnect(m_process, nullptr, this, nullptr);
        stopOwnedProcess(*m_process, m_process->processId());
    }
}

bool AnalyzerRunner::acceptResult(
    int exitCode,
    QProcess::ExitStatus status,
    const QByteArray& output,
    const QByteArray& errorOutput,
    qint64 elapsedMs)
{
    try
    {
        if (status != QProcess::NormalExit || exitCode != 0)
            throw error(QString("analyzer failed (%1): %2").arg(exitCode).arg(safeError(errorOutput)));
        if (output.size() > MaxAnalyzerOutput)
            throw error("analyzer output exceeds 100 MB");
        const StateView current = m_state->view();
        LocalConfig config = LocalConfig::load(current.config.root);
        GraphSnapshot graph = GraphSnapshot::fromNdjson(output, RepositoryPath(config.root));
        if (current.generation && !graph.parseErrorPaths().isEmpty())
            graph = graph.preservingParseErrorsFrom(*current.graph);
        m_state->indexingSucceeded(std::move(config), std::move(graph), elapsedMs);
        return true;
    }
    catch (const std::exception& exception)
    {
        m_state->indexingFailed(QString::fromUtf8(exception.what()), elapsedMs);
        return false;
    }
}

bool AnalyzerRunner::runBlocking(int timeoutMs)
{
    if (m_process)
        return false;
    m_state->indexingStarted();
    const StateView current = m_state->view();
    QStringList argv = m_analyzerArgv;
    const QString program = argv.takeFirst();
    argv << current.config.root;
    if (current.config.configExists)
        argv << "--config" << current.config.path;
    argv << "--format" << "ndjson" << "--trace";
    QProcess process;
    process.setWorkingDirectory(current.config.root);
    ownProcessGroup(process);
    process.start(program, argv);
    const qint64 started = nowMs();
    const bool launched = process.waitForStarted(5'000);
    const qint64 groupId = process.processId();
    if (!launched || !waitForProcess(process, timeoutMs))
    {
        stopOwnedProcess(process, groupId);
        m_state->indexingFailed("analyzer failed to start or timed out", nowMs() - started);
        return false;
    }
    stopOwnedProcess(process, groupId);
    return acceptResult(
        process.exitCode(), process.exitStatus(), process.readAllStandardOutput(), process.readAllStandardError(), nowMs() - started);
}

void AnalyzerRunner::requestReindex()
{
    if (m_process)
    {
        m_dirty = true;
        return;
    }
    startAsync();
}

void AnalyzerRunner::startAsync()
{
    m_state->indexingStarted();
    const StateView current = m_state->view();
    QStringList argv = m_analyzerArgv;
    const QString program = argv.takeFirst();
    argv << current.config.root;
    if (current.config.configExists)
        argv << "--config" << current.config.path;
    argv << "--format" << "ndjson" << "--trace";

    m_output.clear();
    m_errorOutput.clear();
    m_startedMs = nowMs();
    m_process = new QProcess(this);
    m_process->setWorkingDirectory(current.config.root);
    ownProcessGroup(*m_process);
    connect(m_process, &QProcess::started, this, [this]() { m_processGroupId = m_process->processId(); });
    connect(m_process, &QProcess::readyReadStandardOutput, this, [this]() {
        m_output += m_process->readAllStandardOutput();
        if (m_output.size() > MaxAnalyzerOutput)
            stopOwnedProcess(*m_process, m_process->processId());
    });
    connect(m_process, &QProcess::readyReadStandardError, this, [this]() {
        if (m_errorOutput.size() < MaxProcessOutput)
            m_errorOutput += m_process->readAllStandardError().left(MaxProcessOutput - m_errorOutput.size());
        else
            m_process->readAllStandardError();
    });
    connect(m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError processError) {
        if (processError == QProcess::FailedToStart)
        {
            m_timeout.stop();
            m_errorOutput += "analyzer failed to start";
            m_state->indexingFailed(safeError(m_errorOutput), nowMs() - m_startedMs);
            m_process->deleteLater();
            m_process = nullptr;
            m_processGroupId = 0;
            emit generationFinished(false);
            if (m_dirty && !shutdownRequested())
            {
                m_dirty = false;
                startAsync();
            }
        }
    });
    connect(m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
        [this](int exitCode, QProcess::ExitStatus status) {
            m_timeout.stop();
            stopOwnedProcess(*m_process, std::exchange(m_processGroupId, 0));
            m_output += m_process->readAllStandardOutput();
            m_errorOutput += m_process->readAllStandardError();
            const bool success = acceptResult(exitCode, status, m_output, m_errorOutput, nowMs() - m_startedMs);
            m_process->deleteLater();
            m_process = nullptr;
            emit generationFinished(success);
            if (m_dirty && !shutdownRequested())
            {
                m_dirty = false;
                startAsync();
            }
        });
    m_process->start(program, argv);
    m_timeout.start(m_timeoutMs);
}

QByteArray GitInspector::run(const QStringList& arguments, bool allowFailure) const
{
    QProcess process;
    process.setProgram(TrustedGit);
    ownProcessGroup(process);
    QStringList processArguments = {"-C", m_root};
    processArguments.append(arguments);
    hardenGit(process, processArguments);
    process.setArguments(processArguments);
    process.start();
    const bool launched = process.waitForStarted(2'000);
    const qint64 groupId = process.processId();
    if (!launched || !waitForProcess(process, 5'000))
    {
        stopOwnedProcess(process, groupId);
        throw error("git command timed out");
    }
    stopOwnedProcess(process, groupId);
    const QByteArray output = process.readAllStandardOutput();
    if (output.size() > MaxProcessOutput)
        throw error("git output exceeds 2 MB");
    if (!allowFailure && (process.exitStatus() != QProcess::NormalExit || process.exitCode() != 0))
        throw error("git command failed: " + safeError(process.readAllStandardError()));
    if (allowFailure && process.exitCode() != 0)
        return {};
    return output;
}

QString GitInspector::resolveCommit(const QString& ref) const
{
    if (ref.isEmpty() || ref.size() > 200 || ref.contains(QChar::Null) || ref.contains(QRegularExpression("[\\x00-\\x20]")))
        throw error("invalid Git ref");
    const QString resolved = QString::fromUtf8(run({"rev-parse", "--verify", "--end-of-options", ref + "^{commit}"})).trimmed();
    static const QRegularExpression oid("^[0-9a-f]{40,64}$");
    if (!oid.match(resolved).hasMatch())
        throw error("Git ref does not resolve to a commit");
    return resolved;
}

QJsonObject GitInspector::revision(const QString& ref) const
{
    const QString oid = resolveCommit(ref);
    const QList<QByteArray> fields = run({"show", "-s", "--format=%H%x00%an%x00%aI%x00%s", oid, "--"}).trimmed().split('\0');
    if (fields.size() < 4)
        throw error("unexpected Git revision response");
    const QString branch = QString::fromUtf8(run({"symbolic-ref", "--short", "-q", "HEAD"}, true)).trimmed();
    const bool dirty = !run({"status", "--porcelain=v1", "--untracked-files=normal"}).isEmpty();
    return {
        {"ref", ref}, {"commit", QString::fromUtf8(fields[0])}, {"author", QString::fromUtf8(fields[1])},
        {"authoredAt", QString::fromUtf8(fields[2])}, {"subject", QString::fromUtf8(fields[3])},
        {"branch", branch.isEmpty() ? QJsonValue() : QJsonValue(branch)}, {"dirty", dirty},
    };
}

QJsonObject GitInspector::compare(const QString& base, const QString& target) const
{
    if (target != "WORKTREE")
        throw error("compare target must be WORKTREE");
    const QString baseOid = resolveCommit(base.isEmpty() ? "HEAD" : base);
    const QByteArray tracked = run({"diff", "--no-ext-diff", "--relative", "--name-status", "--no-renames", "-z", baseOid, "--"});
    const QByteArray untracked = run({"ls-files", "--others", "--exclude-standard", "-z"});
    QJsonArray files;
    const QList<QByteArray> trackedFields = tracked.split('\0');
    for (int index = 0; index + 1 < trackedFields.size(); index += 2)
    {
        const QByteArray status = trackedFields[index];
        const QByteArray path = trackedFields[index + 1];
        if (status.isEmpty() || path.isEmpty())
            continue;
        files.append(QJsonObject{{"status", QString::fromUtf8(status)}, {"path", QString::fromUtf8(path)}});
    }
    for (const QByteArray& path: untracked.split('\0'))
    {
        if (!path.isEmpty())
            files.append(QJsonObject{{"status", "??"}, {"path", QString::fromUtf8(path)}});
    }
    return {{"base", base}, {"baseCommit", baseOid}, {"target", "WORKTREE"}, {"files", files}, {"fileCount", files.size()}};
}

QJsonObject GitInspector::refs() const
{
    QJsonArray branches;
    const QByteArray branchData = run({"for-each-ref", "--format=%(refname:short)%00%(objectname)", "refs/heads"}, true);
    for (const QByteArray& line: branchData.split('\n'))
    {
        const QList<QByteArray> values = line.split('\0');
        if (values.size() >= 2)
            branches.append(QJsonObject{{"name", QString::fromUtf8(values[0])}, {"commit", QString::fromUtf8(values[1])}});
    }
    QJsonArray commits;
    const QByteArray logData = run({"log", "-n", "20", "--format=%H%x00%h%x00%s%x00%aI"}, true);
    for (const QByteArray& line: logData.split('\n'))
    {
        const QList<QByteArray> values = line.split('\0');
        if (values.size() >= 4)
            commits.append(QJsonObject{{"commit", QString::fromUtf8(values[0])}, {"short", QString::fromUtf8(values[1])},
                                       {"subject", QString::fromUtf8(values[2])}, {"authoredAt", QString::fromUtf8(values[3])}});
    }
    return {{"branches", branches}, {"recentCommits", commits}, {"hasHead", !commits.isEmpty()}};
}

QString GitInspector::unifiedDiff(const QString& base, const QString& relativePath) const
{
    const RepositoryPath paths(m_root);
    const QString clean = paths.relative(relativePath);
    const QString commit = resolveCommit(base.isEmpty() ? "HEAD" : base);
    return QString::fromUtf8(run({"--literal-pathspecs", "diff", "--no-ext-diff", "--unified=3", commit, "--", clean}));
}

GraphComparator::GraphComparator(QString repositoryRoot, QStringList analyzerArgv, QSharedPointer<TraceLog> trace)
    : m_root(RepositoryPath(repositoryRoot).root()), m_analyzerArgv(std::move(analyzerArgv)), m_trace(std::move(trace))
{
}

QSharedPointer<const GraphSnapshot> GraphComparator::snapshot(const QString& commit)
{
    if (m_cache.contains(commit))
        return m_cache.value(commit);
    const qint64 started = nowMs();
    QTemporaryDir temporary(QDir::temp().filePath("code-view-git-snapshot-XXXXXX"));
    if (!temporary.isValid())
        throw error("cannot create temporary Git snapshot directory");
    const QString archive = temporary.filePath("snapshot.tar");
    const QString extractionRoot = temporary.filePath(QFileInfo(m_root).fileName());
    if (!QDir().mkpath(extractionRoot))
        throw error("cannot create temporary Git repository directory");

    auto runProcess = [](const QString& program, const QStringList& arguments, const QString& workingDirectory,
                          int timeoutMs, qsizetype maxOutput) {
        QProcess process;
        process.setWorkingDirectory(workingDirectory);
        ownProcessGroup(process);
        QStringList safeArguments = arguments;
        if (QFileInfo(program).fileName() == "git")
            hardenGit(process, safeArguments);
        process.start(program, safeArguments);
        const bool launched = process.waitForStarted(5'000);
        const qint64 groupId = process.processId();
        if (!launched || !waitForProcess(process, timeoutMs))
        {
            stopOwnedProcess(process, groupId);
            throw error(QFileInfo(program).fileName() + " command timed out");
        }
        stopOwnedProcess(process, groupId);
        const QByteArray output = process.readAllStandardOutput();
        if (output.size() > maxOutput)
            throw error(QFileInfo(program).fileName() + " output exceeds limit");
        if (process.exitStatus() != QProcess::NormalExit || process.exitCode() != 0)
            throw error(QFileInfo(program).fileName() + " command failed: " + safeError(process.readAllStandardError()));
        return output;
    };
    const QString gitRoot = QFileInfo(QString::fromUtf8(runProcess(
        TrustedGit, {"-C", m_root, "rev-parse", "--show-toplevel"}, m_root, 5'000, MaxProcessOutput)).trimmed()).canonicalFilePath();
    if (gitRoot.isEmpty())
        throw error("cannot resolve Git worktree root");
    const QString prefix = QDir(gitRoot).relativeFilePath(m_root).replace('\\', '/');
    if (prefix == ".." || prefix.startsWith("../"))
        throw error("repository is outside its Git worktree root");
    QStringList archiveArguments{"-C", gitRoot, "--literal-pathspecs", "archive", "--format=tar", "--output", archive, commit, "--"};
    if (prefix != ".")
        archiveArguments << prefix;
    runProcess(TrustedGit, archiveArguments, gitRoot, 30'000, MaxProcessOutput);
    if (QFileInfo(archive).size() > 512ll * 1024 * 1024)
        throw error("Git snapshot archive exceeds 512 MB");
    runProcess("/usr/bin/tar", {"-xf", archive, "-C", extractionRoot}, extractionRoot, 30'000, MaxProcessOutput);
    const QString repository = prefix == "." ? extractionRoot : QDir(extractionRoot).filePath(prefix);
    if (!QFileInfo(repository).isDir())
        throw error("Git snapshot did not contain the repository subtree");

    QStringList analyzer = m_analyzerArgv;
    if (analyzer.isEmpty())
        throw error("analyzer argv must not be empty");
    const QString program = analyzer.takeFirst();
    analyzer << repository;
    const QString config = QDir(repository).filePath("code-view.json");
    if (QFileInfo::exists(config))
        analyzer << "--config" << config;
    analyzer << "--format" << "ndjson" << "--trace";
    const QByteArray output = runProcess(program, analyzer, repository, 120'000, MaxAnalyzerOutput);
    auto graph = QSharedPointer<GraphSnapshot>::create(GraphSnapshot::fromNdjson(output, RepositoryPath(repository)));
    m_cache.insert(commit, graph);
    m_trace->write("git.graph-snapshot", {{"commit", commit}, {"durationMs", nowMs() - started},
                                           {"nodes", graph->nodeCount()}, {"edges", graph->edgeCount()}});
    return graph;
}

QJsonObject GraphComparator::compare(const QString& base, const QString& target, const GraphSnapshot& current)
{
    if (target != "WORKTREE")
        throw error("compare target must be WORKTREE");
    const qint64 started = nowMs();
    const QString commit = GitInspector(m_root).resolveCommit(base.isEmpty() ? "HEAD" : base);
    const QSharedPointer<const GraphSnapshot> baseline = snapshot(commit);
    QJsonObject comparison = current.compareTo(*baseline);
    comparison.insert("base", base.isEmpty() ? "HEAD" : base);
    comparison.insert("baseCommit", commit);
    comparison.insert("target", "WORKTREE");
    m_trace->write("git.graph-compare", {{"baseCommit", commit}, {"durationMs", nowMs() - started}});
    return comparison;
}

StartCommandController::StartCommandController(HostState* state, bool allowedByCli, QObject* parent)
    : QObject(parent), m_state(state), m_allowedByCli(allowedByCli)
{
}

StartCommandController::~StartCommandController()
{
    try
    {
        stop();
    }
    catch (...)
    {
    }
}

bool StartCommandController::approvalMatches(const StateView& current) const
{
    return launchableIndex(current) && m_sessionApproved && m_approvedGeneration == current.generation &&
           m_approvedArgv == current.config.startArgv && m_approvedRoot == current.config.root;
}

QJsonObject StartCommandController::approve(quint64 displayedGeneration)
{
    m_sessionApproved = false;
    const StateView current = m_state->view();
    if (!launchableIndex(current))
        throw error("a valid indexed generation is required before command approval");
    if (displayedGeneration != current.generation)
        throw error("command changed since it was displayed; close this dialog and review the current command");
    if (current.config.startArgv.isEmpty() || !current.config.startManual)
        throw error("config does not contain a manual argv start command");
    if (!m_allowedByCli)
        throw error("server was not launched with --allow-command");
    if (m_process && m_process->state() != QProcess::NotRunning)
        throw error("stop the running command before approving another start");
    m_sessionApproved = true;
    m_approvedGeneration = current.generation;
    m_approvedArgv = current.config.startArgv;
    m_approvedRoot = current.config.root;
    m_state->trace()->write("start.approved", {{"generation", qint64(current.generation)}});
    return {{"approved", true}, {"generation", qint64(current.generation)}};
}

QJsonObject StartCommandController::start()
{
    const StateView current = m_state->view();
    if (!launchableIndex(current))
        throw error("a valid indexed generation is required before command start");
    if (current.config.startArgv.isEmpty() || !current.config.startManual || !m_allowedByCli || !approvalMatches(current))
    {
        m_sessionApproved = false;
        throw error("start command requires config manual mode, --allow-command, and session approval");
    }
    if (m_process && m_process->state() != QProcess::NotRunning)
        throw error("start command is already running");
    m_sessionApproved = false;
    if (!m_process)
    {
        m_process = new QProcess(this);
        connect(m_process, &QProcess::started, this, [this]() { m_processGroupId = m_process->processId(); });
        connect(m_process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
            [this](int exitCode, QProcess::ExitStatus exitStatus) {
                m_lastExitCode = exitCode;
                m_lastExitStatus = exitStatus;
                stopProcessGroup(false);
                m_state->trace()->write("start.finished", {{"exitCode", exitCode}, {"normalExit", exitStatus == QProcess::NormalExit}});
            });
    }
    QStringList argv = current.config.startArgv;
    const QString program = argv.takeFirst();
    m_process->setWorkingDirectory(current.config.root);
    m_process->setStandardOutputFile(QProcess::nullDevice());
    m_process->setStandardErrorFile(QProcess::nullDevice());
    ownProcessGroup(*m_process);
    m_process->start(program, argv);
    if (!m_process->waitForStarted(2'000))
        throw error("start command failed to launch");
    m_started = true;
    m_state->trace()->write("start.launched", {{"program", QFileInfo(program).fileName()}});
    QJsonObject result = launch();
    result.insert("started", true);
    return result;
}

QJsonObject StartCommandController::status() const
{
    const StateView current = m_state->view();
    const bool running = m_process && m_process->state() != QProcess::NotRunning;
    return {{"generation", qint64(current.generation)}, {"configured", !current.config.startArgv.isEmpty()},
            {"allowedByCli", m_allowedByCli}, {"approved", approvalMatches(current)}, {"running", running},
            {"pid", running ? QJsonValue(qint64(m_process->processId())) : QJsonValue()},
            {"lastExitCode", m_started && !running ? QJsonValue(m_lastExitCode) : QJsonValue()},
            {"lastExitNormal", m_started && !running ? QJsonValue(m_lastExitStatus == QProcess::NormalExit) : QJsonValue()}};
}

QJsonObject StartCommandController::launch() const
{
    const StateView current = m_state->view();
    QJsonObject result = status();
    result.insert("display", current.config.startDisplay);
    result.insert("argv", QJsonArray::fromStringList(current.config.startArgv));
    result.insert("cwd", current.config.root);
    result.insert("mode", current.config.startMode);
    result.insert("allowLaunch", launchableIndex(current) &&
                                     !current.config.startArgv.isEmpty() && current.config.startManual && m_allowedByCli);
    return result;
}

QJsonObject StartCommandController::stop()
{
    m_sessionApproved = false;
    if (!m_processGroupId)
    {
        QJsonObject result = launch();
        result.insert("stopped", false);
        return result;
    }
    const qint64 pid = m_processGroupId;
    const bool killed = stopProcessGroup(true);
    if (m_process->state() != QProcess::NotRunning && !m_process->waitForFinished(1'000))
        throw error("start command could not be stopped");
    m_state->trace()->write("start.stopped", {{"pid", pid}, {"killed", killed}});
    QJsonObject result = launch();
    result.insert("stopped", true);
    result.insert("stoppedPid", pid);
    result.insert("killed", killed);
    return result;
}

bool StartCommandController::stopProcessGroup(bool strict)
{
    const qint64 pid = std::exchange(m_processGroupId, 0);
    if (!pid)
        return false;
    try
    {
        return stopOwnedProcess(*m_process, pid, strict);
    }
    catch (...)
    {
        m_processGroupId = pid;
        throw;
    }
}

EditorLauncher::EditorLauncher(QString adapter, QString programOverride)
    : m_adapter(std::move(adapter)), m_programOverride(std::move(programOverride))
{
    static const QSet<QString> allowed = {"none", "code", "cursor", "zed", "sublime"};
    if (!allowed.contains(m_adapter))
        throw error("unsupported editor adapter");
}

QJsonObject EditorLauncher::open(const RepositoryPath& paths, const QString& relativePath, int line, int column) const
{
    if (m_adapter == "none")
        throw error("no editor adapter selected; launch with --editor code|cursor|zed|sublime");
    if (line < 1 || line > 1'000'000 || column < 1 || column > 1'000'000)
        throw error("line and column must be positive integers");
    const QString absolute = paths.resolve(relativePath);
    if (!QFileInfo(absolute).isFile())
        throw error("editor target is not a file");
    QString program = m_programOverride;
    if (program.isEmpty())
        program = m_adapter == "sublime" ? "subl" : m_adapter;
    QStringList arguments;
    const QString location = QString("%1:%2:%3").arg(absolute).arg(line).arg(column);
    if (m_adapter == "code" || m_adapter == "cursor")
        arguments << "--goto" << location;
    else
        arguments << location;
    qint64 pid = 0;
    if (!QProcess::startDetached(program, arguments, paths.root(), &pid))
        throw error("editor adapter failed to launch");
    return {{"opened", true}, {"adapter", m_adapter}, {"path", paths.relative(relativePath)}, {"line", line}, {"column", column}, {"pid", pid}};
}

RepositoryWatcher::RepositoryWatcher(HostState* state, AnalyzerRunner* runner, QObject* parent)
    : QObject(parent), m_state(state), m_runner(runner)
{
    m_poll.setInterval(1'000);
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(300);
    connect(&m_poll, &QTimer::timeout, this, &RepositoryWatcher::scan);
    connect(&m_debounce, &QTimer::timeout, m_runner, &AnalyzerRunner::requestReindex);
    connect(&m_watcher, &QFileSystemWatcher::fileChanged, this, [this]() { schedule(); });
    connect(&m_watcher, &QFileSystemWatcher::directoryChanged, this, [this]() { schedule(); });
    const StateView current = m_state->view();
    m_watcher.addPath(current.config.root);
    if (current.config.configExists)
        m_watcher.addPath(current.config.path);
    m_manifest = scanNowForTests();
    m_poll.start();
}

void RepositoryWatcher::addDirectory(const QDir& directory, const LocalConfig& config, QCryptographicHash& hash) const
{
    const QFileInfoList entries = directory.entryInfoList(
        QDir::AllEntries | QDir::NoDotAndDotDot | QDir::Hidden, QDir::Name | QDir::DirsFirst);
    for (const QFileInfo& entry: entries)
    {
        if (entry.isSymLink())
            continue;
        const QString relative = QDir(config.root).relativeFilePath(entry.absoluteFilePath()).replace('\\', '/');
        if (config.isExcluded(relative))
            continue;
        if (entry.isDir())
            addDirectory(QDir(entry.absoluteFilePath()), config, hash);
        else if (entry.fileName() == "code-view.json" || entry.suffix() == "py")
        {
            hash.addData(relative.toUtf8());
            hash.addData(QByteArray::number(entry.size()));
            hash.addData(QByteArray::number(entry.lastModified().toMSecsSinceEpoch()));
        }
    }
}

QByteArray RepositoryWatcher::scanNowForTests() const
{
    const LocalConfig config = m_state->view().config;
    QCryptographicHash hash(QCryptographicHash::Sha256);
    addDirectory(QDir(config.root), config, hash);
    return hash.result();
}

void RepositoryWatcher::scan()
{
    const StateView current = m_state->view();
    if (current.config.configExists && !m_watcher.files().contains(current.config.path))
        m_watcher.addPath(current.config.path);
    const QByteArray next = scanNowForTests();
    if (next != m_manifest)
    {
        m_manifest = next;
        schedule();
    }
}

void RepositoryWatcher::schedule()
{
    m_state->trace()->write("watch.change-detected");
    m_debounce.start();
}
