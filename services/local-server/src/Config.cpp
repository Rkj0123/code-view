#include "Config.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QRegularExpression>
#include <QSet>

#include <stdexcept>
#include <algorithm>
#include <limits>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace
{
std::runtime_error error(const QString& message)
{
    return std::runtime_error(message.toStdString());
}

QStringList stringArray(const QJsonValue& value, const QString& field, bool allowEmpty = false)
{
    if (!value.isArray())
        throw error(field + " must be an array");
    QStringList result;
    for (const QJsonValue item: value.toArray())
    {
        if (!item.isString() || item.toString().isEmpty() || item.toString().contains(QChar::Null))
            throw error(field + " must contain non-empty strings");
        result.push_back(item.toString());
    }
    if (!allowEmpty && result.isEmpty())
        throw error(field + " must not be empty");
    return result;
}

void rejectUnknown(const QJsonObject& object, const QSet<QString>& allowed, const QString& field)
{
    for (auto it = object.begin(); it != object.end(); ++it)
        if (!allowed.contains(it.key()))
            throw error("unknown " + field + " key: " + it.key());
}

QStringList documentArgv(const QString& command, const QString& field)
{
    if (command.isEmpty() || command.contains(QChar::Null))
        throw error(field + " must be a non-empty string");
    const QStringList argv = QProcess::splitCommand(command);
    if (argv.isEmpty())
        throw error(field + " must contain a command");
    return argv;
}
}

RepositoryPath::RepositoryPath(const QString& root)
{
    m_root = QFileInfo(root).canonicalFilePath();
    if (m_root.isEmpty() || !QFileInfo(m_root).isDir())
        throw error("repository root is not a directory");
    m_rootDescriptor = ::open(QFile::encodeName(m_root).constData(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (m_rootDescriptor < 0)
        throw error("cannot open repository directory");
}

RepositoryPath::~RepositoryPath()
{
    ::close(m_rootDescriptor);
}

QString RepositoryPath::relative(const QString& value) const
{
    if (value.isEmpty() || value.contains(QChar::Null) || value.contains('\\') || QDir::isAbsolutePath(value))
        throw error("path must be a non-empty repository-relative path");
    const QString clean = QDir::cleanPath(value).replace('\\', '/');
    if (clean != value || clean == ".." || clean.startsWith("../") || clean.contains("/../"))
        throw error("path must be normalized inside the repository");
    return clean;
}

QString RepositoryPath::resolve(const QString& value, bool mustExist) const
{
    const QString clean = relative(value);
    const QFileInfo info(QDir(m_root).filePath(clean));
    QString resolved = info.canonicalFilePath();
    if (resolved.isEmpty())
    {
        if (mustExist)
            throw error("repository path does not exist: " + clean);
        const QString parent = QFileInfo(info.absolutePath()).canonicalFilePath();
        if (parent.isEmpty())
            throw error("repository path parent does not exist: " + clean);
        resolved = QDir(parent).filePath(info.fileName());
    }
    if (resolved != m_root && !resolved.startsWith(m_root + '/'))
        throw error("path escapes repository through a symlink");
    return resolved;
}

QByteArray RepositoryPath::readFile(const QString& value, qsizetype maxBytes) const
{
    if (maxBytes < 0 || maxBytes == std::numeric_limits<qsizetype>::max())
        throw error("file size limit is invalid");
    const QStringList parts = relative(value).split('/');
    int descriptor = ::fcntl(m_rootDescriptor, F_DUPFD_CLOEXEC, 0);
    if (descriptor < 0)
        throw error("cannot open repository directory");
    for (qsizetype index = 0; index < parts.size(); ++index)
    {
        const int flags = O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK | (index + 1 < parts.size() ? O_DIRECTORY : 0);
        const int next = ::openat(descriptor, QFile::encodeName(parts[index]).constData(), flags);
        ::close(descriptor);
        descriptor = next;
        if (descriptor < 0)
            throw error("file cannot be read without following symlinks");
    }
    struct stat metadata {};
    if (::fstat(descriptor, &metadata) != 0 || !S_ISREG(metadata.st_mode) || metadata.st_size > maxBytes)
    {
        ::close(descriptor);
        throw error("file is not regular or exceeds the size limit");
    }
    QFile file;
    if (!file.open(descriptor, QIODevice::ReadOnly, QFileDevice::AutoCloseHandle))
    {
        ::close(descriptor);
        throw error("file cannot be read");
    }
    const QByteArray data = file.read(maxBytes + 1);
    if (data.size() > maxBytes || file.error() != QFileDevice::NoError)
        throw error("file cannot be read or exceeds the size limit");
    return data;
}

LocalConfig LocalConfig::load(const QString& root)
{
    RepositoryPath guard(root);
    const QString candidate = QDir(guard.root()).filePath("code-view.json");
    if (!QFileInfo::exists(candidate))
        return {guard.root(), candidate, "python", {}, false, false, {}, false, {}, "document", {}};
    const QString path = candidate;
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(guard.readFile("code-view.json", 1024 * 1024), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject())
        throw error("code-view.json must contain a JSON object");
    const QJsonObject object = document.object();
    const QSet<QString> allowed = {
        "schemaVersion", "entry", "startCommand", "languages", "includeTests", "index", "canvas", "git"};
    rejectUnknown(object, allowed, "code-view.json");
    if (object.contains("schemaVersion") &&
        (!object.value("schemaVersion").isDouble() || object.value("schemaVersion").toDouble() != 1.0))
        throw error("only config version 1 is supported");

    QJsonObject entrypoint;
    QStringList startArgv;
    bool startManual = false;
    QString startDisplay;
    QString startMode = "document";
    const QJsonValue entry = object.value("entry");
    if (entry.isString())
    {
        guard.resolve(entry.toString());
        entrypoint = {{"file", entry.toString()}};
    }
    else if (entry.isObject())
    {
        const QJsonObject nested = entry.toObject();
        rejectUnknown(nested, {"file", "symbol", "command"}, "entry");
        if (!nested.value("file").isString() || nested.value("file").toString().isEmpty())
            throw error("entry.file must be a non-empty string");
        guard.resolve(nested.value("file").toString());
        entrypoint = {{"file", nested.value("file").toString()}};
        if (nested.contains("symbol"))
        {
            if (!nested.value("symbol").isString() || nested.value("symbol").toString().isEmpty())
                throw error("entry.symbol must be a non-empty string");
            entrypoint.insert("symbol", nested.value("symbol"));
        }
        if (nested.contains("command"))
        {
            const QJsonValue commandValue = nested.value("command");
            if (commandValue.isString())
            {
                startDisplay = commandValue.toString();
                startArgv = documentArgv(startDisplay, "entry.command");
            }
            else if (commandValue.isObject())
            {
                const QJsonObject command = commandValue.toObject();
                const QStringList commandKeys = command.keys();
                if (QSet<QString>(commandKeys.begin(), commandKeys.end()) != QSet<QString>({"argv", "mode"}))
                    throw error("entry.command requires only argv and mode");
                startMode = command.value("mode").toString();
                if (startMode != "manual" && startMode != "document")
                    throw error("entry.command.mode must be manual or document");
                startArgv = stringArray(command.value("argv"), "entry.command.argv");
                startDisplay = startArgv.join(' ');
                startManual = startMode == "manual";
            }
            else
                throw error("entry.command must be a string or command object");
        }
    }
    else if (!entry.isUndefined())
        throw error("entry must be a path string or entry object");

    const QJsonValue startCommand = object.value("startCommand");
    if (!startCommand.isUndefined() && !startCommand.isString() && !startCommand.isObject())
        throw error("startCommand must be a string or command object");
    QStringList fallbackArgv;
    QString fallbackDisplay;
    QString fallbackMode = "document";
    bool fallbackManual = false;
    if (startCommand.isObject())
    {
        const QJsonObject command = startCommand.toObject();
        const QStringList commandKeys = command.keys();
        if (QSet<QString>(commandKeys.begin(), commandKeys.end()) != QSet<QString>({"argv", "mode"}))
            throw error("startCommand object requires only argv and mode");
        fallbackMode = command.value("mode").toString();
        if (fallbackMode != "manual" && fallbackMode != "document")
            throw error("startCommand.mode must be manual or document");
        fallbackArgv = stringArray(command.value("argv"), "startCommand.argv");
        fallbackDisplay = fallbackArgv.join(' ');
        fallbackManual = fallbackMode == "manual";
    }
    else if (startCommand.isString())
    {
        fallbackDisplay = startCommand.toString();
        fallbackArgv = documentArgv(fallbackDisplay, "startCommand");
    }
    if (entrypoint.isEmpty() && startCommand.isString())
        entrypoint = {{"commandText", startCommand.toString()}};
    if (startArgv.isEmpty() && !fallbackArgv.isEmpty())
    {
        startArgv = fallbackArgv;
        startDisplay = fallbackDisplay;
        startMode = fallbackMode;
        startManual = fallbackManual;
    }

    if (object.contains("languages"))
    {
        const QJsonArray languages = object.value("languages").toArray();
        if (!object.value("languages").isArray() || languages.size() != 1 || languages.first().toString() != "python")
            throw error("languages must be exactly [\"python\"]");
    }

    const QJsonValue includeTestsValue = object.value("includeTests");
    if (!includeTestsValue.isUndefined() && !includeTestsValue.isBool())
        throw error("includeTests must be true or false");

    bool includeTests = includeTestsValue.toBool(false);
    QStringList excludePatterns;
    QString modules = "hide";
    if (object.value("index").isObject())
    {
        const QJsonObject index = object.value("index").toObject();
        rejectUnknown(index, {"tests", "modules", "externalPackages", "exclude"}, "index");
        const QString tests = index.value("tests").toString();
        if (index.contains("tests") && tests != "include" && tests != "exclude")
            throw error("index.tests must be include or exclude");
        if (!tests.isEmpty())
            includeTests = tests == "include";
        modules = index.contains("modules") ? index.value("modules").toString() : "hide";
        if (modules != "hide" && modules != "show")
            throw error("index.modules must be hide or show");
        const QString external = index.contains("externalPackages") ? index.value("externalPackages").toString() : "collapse";
        if (external != "collapse")
            throw error("index.externalPackages must be collapse");
        if (index.contains("exclude"))
            excludePatterns = stringArray(index.value("exclude"), "index.exclude", true);
        QSet<QString> unique;
        for (const QString& pattern: excludePatterns)
        {
            if (unique.contains(pattern))
                throw error("index.exclude must not contain duplicates");
            unique.insert(pattern);
            if (QDir::isAbsolutePath(pattern) || pattern.contains(QChar::Null) ||
                QDir::cleanPath(pattern) == ".." || QDir::cleanPath(pattern).startsWith("../"))
                throw error("index.exclude patterns must stay inside the repository");
        }
    }
    else if (object.contains("index"))
        throw error("index must be an object");

    QString initialView = "entry-focus";
    const QStringList relationshipKinds = {"contains", "imports", "calls", "inherits", "constructs", "reads", "writes",
                                           "decorates", "type_uses", "api_calls", "test_covers"};
    QStringList enabledRelationships = relationshipKinds.mid(0, relationshipKinds.size() - 1);
    if (object.contains("canvas"))
    {
        if (!object.value("canvas").isObject())
            throw error("canvas must be an object");
        const QJsonObject canvas = object.value("canvas").toObject();
        rejectUnknown(canvas, {"initialView", "relationships"}, "canvas");
        initialView = canvas.contains("initialView") ? canvas.value("initialView").toString() : "entry-focus";
        if (initialView != "entry-focus" && initialView != "whole-repo")
            throw error("canvas.initialView must be entry-focus or whole-repo");
        const QSet<QString> relationships(relationshipKinds.begin(), relationshipKinds.end());
        const QJsonValue value = canvas.value("relationships");
        if (!value.isUndefined())
        {
            QSet<QString> seen;
            if (value.isArray())
            {
                enabledRelationships.clear();
                for (const QJsonValue& item: value.toArray())
                {
                    const QString kind = item.toString();
                    if (!item.isString() || !relationships.contains(kind) || seen.contains(kind))
                        throw error("canvas.relationships must contain unique supported relationship names");
                    seen.insert(kind);
                    enabledRelationships.append(kind);
                }
            }
            else if (value.isObject())
            {
                const QJsonObject flags = value.toObject();
                rejectUnknown(flags, relationships, "canvas.relationships");
                for (auto it = flags.begin(); it != flags.end(); ++it)
                    if (!it.value().isBool())
                        throw error("canvas.relationships flags must be boolean");
                enabledRelationships.clear();
                for (const QString& kind: relationshipKinds)
                    if (flags.value(kind).toBool(kind != "test_covers"))
                        enabledRelationships.append(kind);
            }
            else
                throw error("canvas.relationships must be an array or object");
        }
    }
    QString gitBase = "HEAD";
    if (object.contains("git"))
    {
        if (!object.value("git").isObject())
            throw error("git must be an object");
        const QJsonObject git = object.value("git").toObject();
        rejectUnknown(git, {"base"}, "git");
        if (git.contains("base") && (!git.value("base").isString() || git.value("base").toString().isEmpty()))
            throw error("git.base must be a non-empty string");
        gitBase = git.value("base").toString("HEAD");
    }
    return {guard.root(), path, "python", entrypoint, true, includeTests, startArgv, startManual,
            startDisplay, startMode, excludePatterns, modules, initialView, enabledRelationships, gitBase};
}

bool LocalConfig::isExcluded(const QString& relativePath) const
{
    const QString clean = QDir::cleanPath(relativePath).replace('\\', '/');
    const QStringList parts = clean.split('/');
    static const QSet<QString> ignored = {
        ".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".tox", ".nox", "site-packages"};
    if (std::any_of(parts.begin(), parts.end(), [](const QString& part) {
            return ignored.contains(part) || part.startsWith('.');
        }))
        return true;
    for (const QString& pattern: excludePatterns)
        if (QRegularExpression(QRegularExpression::wildcardToRegularExpression(pattern)).match(clean).hasMatch())
            return true;
    return !includeTests && (parts.contains("test", Qt::CaseInsensitive) ||
                            parts.contains("tests", Qt::CaseInsensitive) ||
                            QFileInfo(clean).fileName().startsWith("test_") ||
                            QFileInfo(clean).fileName().endsWith("_test.py"));
}

QJsonObject LocalConfig::publicJson() const
{
    return {
        {"schemaVersion", 1},
        {"language", language},
        {"entrypoint", entrypoint},
        {"configExists", configExists},
        {"includeTests", includeTests},
        {"startCommandConfigured", !startArgv.isEmpty()},
        {"startCommandDisplay", startDisplay},
        {"startCommandArgv", QJsonArray::fromStringList(startArgv)},
        {"startCommandMode", startMode},
        {"modules", modules},
        {"initialView", initialView},
        {"relationships", QJsonArray::fromStringList(relationships)},
        {"gitBase", gitBase},
    };
}
