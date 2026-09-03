#include "HttpHost.h"

#include <QCryptographicHash>
#include <QFile>
#include <QFileInfo>
#include <QHttpHeaders>
#include <QJsonDocument>
#include <QMimeDatabase>
#include <QRandomGenerator>
#include <QUrlQuery>

#include <stdexcept>

namespace
{
constexpr auto ContentSecurityPolicy =
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'";

QByteArray randomToken()
{
    QByteArray bytes(32, Qt::Uninitialized);
    QRandomGenerator::system()->fillRange(reinterpret_cast<quint32*>(bytes.data()), 8);
    return bytes.toHex();
}

int integerQuery(const QUrlQuery& query, const QString& name, int fallback)
{
    const QString value = query.queryItemValue(name);
    if (value.isEmpty())
        return fallback;
    bool ok = false;
    const int result = value.toInt(&ok);
    if (!ok)
        throw std::runtime_error((name + " must be an integer").toStdString());
    return result;
}

QJsonObject bodyObject(const QHttpServerRequest& request)
{
    if (request.body().size() > 4'096)
        throw std::runtime_error("request body exceeds 4 KB");
    QJsonParseError parseError;
    const QJsonDocument document = QJsonDocument::fromJson(request.body(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isObject())
        throw std::runtime_error("request body must be a JSON object");
    return document.object();
}


int bodyInteger(const QJsonObject& body, const QString& key)
{
    const QJsonValue value = body.value(key);
    if (!value.isDouble())
        throw std::runtime_error((key + " must be an integer").toStdString());
    const double number = value.toDouble();
    const int integer = value.toInt(0);
    if (double(integer) != number)
        throw std::runtime_error((key + " must be an integer").toStdString());
    return integer;
}
}

HttpHost::HttpHost(
    HostState* state,
    AnalyzerRunner* analyzer,
    StartCommandController* commands,
    EditorLauncher editor,
    QString webRoot,
    QObject* parent)
    : QObject(parent)
    , m_state(state)
    , m_analyzer(analyzer)
    , m_commands(commands)
    , m_editor(std::move(editor))
    , m_paths(m_state->view().config.root)
    , m_token(randomToken())
    , m_comparator(std::make_unique<GraphComparator>(m_state->view().config.root, m_analyzer->analyzerArgv(), m_state->trace()))
{
    if (!webRoot.isEmpty())
        m_assets = std::make_unique<RepositoryPath>(webRoot);
    addRoutes();
    m_http.addAfterRequestHandler(this, [this](const QHttpServerRequest& request, QHttpServerResponse& response) {
        QHttpHeaders headers = response.headers();
        headers.replaceOrAppend("Content-Security-Policy", ContentSecurityPolicy);
        headers.replaceOrAppend("Cache-Control", "no-store");
        headers.replaceOrAppend("X-Content-Type-Options", "nosniff");
        headers.replaceOrAppend("Referrer-Policy", "no-referrer");
        headers.replaceOrAppend("Cross-Origin-Resource-Policy", "same-origin");
        headers.replaceOrAppend("X-Frame-Options", "DENY");
        response.setHeaders(std::move(headers));
        m_state->trace()->write("http.request", {
            {"method", int(request.method())},
            {"path", request.url().path()},
            {"status", int(response.statusCode())},
        });
    });
    m_http.setMissingHandler(this, [this](const QHttpServerRequest& request, QHttpServerResponder& responder) {
        QHttpServerResponse response = guard(request, true);
        if (!isGuardFailure(response))
            response = failure("not found", Status::NotFound);
        responder.sendResponse(response);
    });
}

bool HttpHost::listen(quint16 port)
{
    if (!m_tcp.listen(QHostAddress::LocalHost, port))
    {
        m_listenError = "loopback listen failed: " + m_tcp.errorString();
        return false;
    }
    if (!m_http.bind(&m_tcp))
    {
        m_listenError = "QHttpServer refused the loopback TCP server";
        m_tcp.close();
        return false;
    }
    m_listenError.clear();
    m_state->trace()->write("server.listening", {{"address", "127.0.0.1"}, {"port", int(m_tcp.serverPort())}});
    return true;
}

QString HttpHost::origin() const
{
    return QString("http://127.0.0.1:%1").arg(port());
}

QString HttpHost::launchUrl() const
{
    return origin() + "/?token=" + QString::fromLatin1(m_token);
}

bool HttpHost::secureEquals(const QByteArray& left, const QByteArray& right) const
{
    if (left.size() != right.size())
        return false;
    unsigned char difference = 0;
    for (qsizetype index = 0; index < left.size(); ++index)
        difference |= static_cast<unsigned char>(left[index] ^ right[index]);
    return difference == 0;
}

QHttpServerResponse HttpHost::json(QJsonObject value, Status status) const
{
    return secured(QHttpServerResponse("application/json", QJsonDocument(value).toJson(QJsonDocument::Compact), status));
}

QHttpServerResponse HttpHost::secured(QHttpServerResponse response) const
{
    QHttpHeaders headers = response.headers();
    headers.replaceOrAppend("Content-Security-Policy", ContentSecurityPolicy);
    headers.replaceOrAppend("Cache-Control", "no-store");
    headers.replaceOrAppend("X-Content-Type-Options", "nosniff");
    headers.replaceOrAppend("Referrer-Policy", "no-referrer");
    headers.replaceOrAppend("Cross-Origin-Resource-Policy", "same-origin");
    headers.replaceOrAppend("X-Frame-Options", "DENY");
    response.setHeaders(std::move(headers));
    return response;
}

QHttpServerResponse HttpHost::failure(const QString& message, Status status) const
{
    return json({{"error", message}}, status);
}

QHttpServerResponse HttpHost::guard(const QHttpServerRequest& request, bool requireBearer) const
{
    if (!request.remoteAddress().isLoopback())
        return failure("loopback clients only", Status::Forbidden);
    const QByteArray host = request.value("Host").toLower();
    const QByteArray portText = QByteArray::number(port());
    if (host != "127.0.0.1:" + portText && host != "localhost:" + portText)
        return failure("invalid Host header", Status::Forbidden);
    const QByteArray originHeader = request.value("Origin");
    if (!originHeader.isEmpty() && originHeader != origin().toUtf8() &&
        originHeader != ("http://localhost:" + portText))
        return failure("invalid Origin header", Status::Forbidden);
    if (requireBearer)
    {
        const QByteArray authorization = request.value("Authorization");
        if (!authorization.startsWith("Bearer ") || !secureEquals(authorization.mid(7), m_token))
            return failure("valid bearer token required", Status::Unauthorized);
    }
    return QHttpServerResponse(Status::NoContent);
}

bool HttpHost::isGuardFailure(const QHttpServerResponse& response) const
{
    return response.statusCode() != Status::NoContent;
}

QHttpServerResponse HttpHost::staticFile(const QString& relative) const
{
    if (!m_assets)
    {
        if (relative != "index.html")
            return failure("web assets are not built", Status::NotFound);
        return secured(QHttpServerResponse(
            "text/html; charset=utf-8",
            "<!doctype html><meta charset=utf-8><title>Code View</title><h1>Code View local server is ready.</h1>"));
    }
    if (relative.isEmpty() || relative.contains("..") || relative.startsWith('/'))
        return failure("invalid asset path", Status::Forbidden);
    try
    {
        const QByteArray data = m_assets->readFile(relative, 10 * 1024 * 1024);
        const QByteArray mime = QMimeDatabase().mimeTypeForFile(relative, QMimeDatabase::MatchExtension).name().toUtf8();
        return secured(QHttpServerResponse(mime, data));
    }
    catch (const std::exception&)
    {
        return failure("asset cannot be read", Status::NotFound);
    }
}

QHttpServerResponse HttpHost::spa(const QHttpServerRequest& request) const
{
    QHttpServerResponse guarded = guard(request, false);
    if (isGuardFailure(guarded))
        return guarded;
    if (request.url().path() == "/")
    {
        const QByteArray queryToken = QUrlQuery(request.url()).queryItemValue("token").toLatin1();
        if (!queryToken.isEmpty() && !secureEquals(queryToken, m_token))
            return failure("invalid launch token", Status::Unauthorized);
    }
    return staticFile("index.html");
}

QJsonObject HttpHost::wrapGraph(const StateView& state, QJsonObject graph, QJsonValue comparison) const
{
    return {
        {"generation", qint64(state.generation)},
        {"comparison", comparison},
        {"projectName", QFileInfo(state.config.root).fileName()},
        {"config", state.config.publicJson()},
        {"graph", graph},
    };
}

void HttpHost::addRoutes()
{
    using Method = QHttpServerRequest::Method;
    auto api = [this](const QHttpServerRequest& request, auto&& operation, Status errorStatus = Status::BadRequest) {
        QHttpServerResponse guarded = guard(request, true);
        if (isGuardFailure(guarded))
            return guarded;
        try
        {
            return json(operation());
        }
        catch (const std::exception& exception)
        {
            return failure(QString::fromUtf8(exception.what()), errorStatus);
        }
    };

    m_http.route("/", Method::Get, [this](const QHttpServerRequest& request) { return spa(request); });
    for (const QString& route: {"/repository", "/entry", "/compare", "/settings"})
        m_http.route(route, Method::Get, [this](const QHttpServerRequest& request) { return spa(request); });
    m_http.route("/node/<arg>", Method::Get,
        [this](const QString&, const QHttpServerRequest& request) { return spa(request); });
    m_http.route("/assets/<arg>", Method::Get,
        [this](const QString& name, const QHttpServerRequest& request) -> QHttpServerResponse {
            QHttpServerResponse guarded = guard(request, false);
            if (isGuardFailure(guarded))
                return guarded;
            return staticFile("assets/" + name);
        });
    m_http.route("/<arg>", Method::Get,
        [this](const QString& name, const QHttpServerRequest& request) -> QHttpServerResponse {
            QHttpServerResponse guarded = guard(request, false);
            if (isGuardFailure(guarded))
                return guarded;
            return staticFile(name);
        });

    m_http.route("/api/v1/project", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this]() {
            const StateView state = m_state->view();
            return QJsonObject{
                {"generation", qint64(state.generation)}, {"projectName", QFileInfo(state.config.root).fileName()},
                {"config", state.config.publicJson()}, {"project", state.graph->project()},
                {"nodeCount", state.graph->nodeCount()}, {"edgeCount", state.graph->edgeCount()},
            };
        });
    });
    m_http.route("/api/v1/status", Method::Get,
        [this, api](const QHttpServerRequest& request) { return api(request, [this]() { return m_state->statusJson(); }); });
    m_http.route("/api/v1/graph", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const StateView state = m_state->view();
            const QUrlQuery query(request.url());
            const QString root = query.queryItemValue("root");
            if (root.isEmpty())
            {
                QJsonValue comparison;
                const QString base = query.queryItemValue("base").isEmpty() ? "HEAD" : query.queryItemValue("base");
                try
                {
                    comparison = m_comparator->compare(base, "WORKTREE", *state.graph);
                }
                catch (const std::exception& exception)
                {
                    m_state->trace()->write("git.graph-comparison-unavailable", {{"base", base}, {"error", QString::fromUtf8(exception.what())}});
                }
                return wrapGraph(state, state.graph->view(query.queryItemValue("view", QUrl::FullyDecoded).isEmpty()
                                                              ? "entry"
                                                              : query.queryItemValue("view")), comparison);
            }
            const int depth = integerQuery(query, "depth", 2);
            const int limit = integerQuery(query, "limit", 500);
            const QString direction = query.queryItemValue("direction").isEmpty() ? "both" : query.queryItemValue("direction");
            QSet<QString> kinds;
            for (const QString& kind: query.queryItemValue("kinds").split(',', Qt::SkipEmptyParts))
                kinds.insert(kind);
            QJsonObject focused = state.graph->graph(root, depth, direction, limit, kinds);
            focused.insert("schemaVersion", "code-view.graph/v1");
            focused.insert("project", state.graph->project());
            focused.insert("diagnostics", QJsonArray());
            return wrapGraph(state, focused);
        });
    });
    m_http.route("/api/v1/node", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const StateView state = m_state->view();
            const QUrlQuery query(request.url());
            QJsonObject node = state.graph->node(
                query.queryItemValue("id"), integerQuery(query, "incomingCursor", 0),
                integerQuery(query, "outgoingCursor", 0), integerQuery(query, "limit", 100),
                query.queryItemValue("showAll") == "true");
            node.insert("generation", qint64(state.generation));
            return node;
        }, Status::NotFound);
    });
    m_http.route("/api/v1/search", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const StateView state = m_state->view();
            const QUrlQuery query(request.url());
            return QJsonObject{{"generation", qint64(state.generation)},
                               {"results", state.graph->search(query.queryItemValue("q"), integerQuery(query, "limit", 50))}};
        });
    });
    m_http.route("/api/v1/source", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const StateView state = m_state->view();
            const QUrlQuery query(request.url());
            const QString relative = m_paths.relative(query.queryItemValue("path", QUrl::FullyDecoded));
            if (!state.graph->containsFile(relative))
                throw std::runtime_error("source path is not present in the active graph");
            const QStringList lines = QString::fromUtf8(m_paths.readFile(relative, 5 * 1024 * 1024)).split('\n');
            const int start = integerQuery(query, "startLine", 1);
            const int end = integerQuery(query, "endLine", qMin(lines.size(), start + 199));
            if (start < 1 || start > lines.size() || end < start || end - start + 1 > 400)
                throw std::runtime_error("source range must contain 1 to 400 lines");
            return QJsonObject{{"generation", qint64(state.generation)}, {"path", relative}, {"startLine", start},
                               {"endLine", qMin(end, lines.size())}, {"text", lines.mid(start - 1, end - start + 1).join('\n')}};
        });
    });
    m_http.route("/api/v1/git/revision", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const QString ref = QUrlQuery(request.url()).queryItemValue("ref");
            return GitInspector(m_state->view().config.root).revision(ref.isEmpty() ? "HEAD" : ref);
        }, Status::Conflict);
    });
    m_http.route("/api/v1/compare", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const QUrlQuery query(request.url());
            const StateView state = m_state->view();
            const QString base = query.queryItemValue("base").isEmpty() ? "HEAD" : query.queryItemValue("base");
            const QString target = query.queryItemValue("target").isEmpty() ? "WORKTREE" : query.queryItemValue("target");
            return QJsonObject{{"generation", qint64(state.generation)},
                               {"files", GitInspector(state.config.root).compare(base, target)},
                               {"graph", m_comparator->compare(base, target, *state.graph)}};
        }, Status::Conflict);
    });
    m_http.route("/api/v1/git/diff", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const QUrlQuery query(request.url());
            const QString nodeId = query.queryItemValue("node");
            if (nodeId.isEmpty())
                throw std::runtime_error("node is required");
            const StateView state = m_state->view();
            const QString base = query.queryItemValue("base").isEmpty() ? "HEAD" : query.queryItemValue("base");
            const QJsonObject comparison = m_comparator->compare(base, "WORKTREE", *state.graph);
            const QJsonObject nodeGroups = comparison.value("nodes").toObject();
            for (const QString& status: {"added", "removed", "modified", "unchanged"})
            {
                for (const QJsonValue& value: nodeGroups.value(status).toArray())
                {
                    if (value.toObject().value("id").toString() == nodeId)
                    {
                        if (status == "unchanged")
                            throw std::runtime_error("node is unchanged");
                        const QJsonObject item = value.toObject();
                        const QJsonObject document = item.value("current").isObject()
                                                         ? item.value("current").toObject()
                                                         : item.value("base").toObject();
                        const QString path = document.value("path").toString();
                        const QString unified = path.isEmpty() ? QString() : GitInspector(state.config.root).unifiedDiff(base, path);
                        return QJsonObject{{"generation", qint64(state.generation)}, {"base", base},
                                           {"target", "WORKTREE"}, {"state", status}, {"unified", unified}, {"node", item}};
                    }
                }
            }
            throw std::runtime_error("node is not present in the comparison");
        }, Status::NotFound);
    });
    m_http.route("/api/v1/git/refs", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this]() { return GitInspector(m_state->view().config.root).refs(); });
    });
    m_http.route("/api/v1/reindex", Method::Post, [this, api](const QHttpServerRequest& request) {
        return api(request, [this]() { m_analyzer->requestReindex(); return QJsonObject{{"accepted", true}}; });
    });
    m_http.route("/api/v1/launch", Method::Get, [this, api](const QHttpServerRequest& request) {
        return api(request, [this]() { return m_commands->launch(); });
    });
    m_http.route("/api/v1/launch/approve", Method::Post, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const QJsonObject body = bodyObject(request);
            const QStringList keys = body.keys();
            const QJsonValue value = body.value("generation");
            const qint64 generation = value.toInteger(-1);
            if (QSet<QString>(keys.begin(), keys.end()) != QSet<QString>({"schemaVersion", "generation"}) ||
                body.value("schemaVersion").toString() != "code-view.launch-approval/v2" ||
                !value.isDouble() || generation < 1 || generation > 9'007'199'254'740'991ll ||
                double(generation) != value.toDouble())
                throw std::runtime_error("approval requires v2 schema and the displayed launch generation");
            return m_commands->approve(quint64(generation));
        }, Status::Conflict);
    });
    m_http.route("/api/v1/launch/start", Method::Post, [this, api](const QHttpServerRequest& request) {
        return api(request, [this]() { return m_commands->start(); }, Status::Conflict);
    });
    m_http.route("/api/v1/launch/stop", Method::Post, [this, api](const QHttpServerRequest& request) {
        return api(request, [this]() { return m_commands->stop(); }, Status::Conflict);
    });
    m_http.route("/api/v1/editor/open", Method::Post, [this, api](const QHttpServerRequest& request) {
        return api(request, [this, &request]() {
            const QJsonObject body = bodyObject(request);
            const QStringList bodyKeys = body.keys();
            if (QSet<QString>(bodyKeys.begin(), bodyKeys.end()) != QSet<QString>({"path", "line", "column"}) ||
                !body.value("path").isString())
                throw std::runtime_error("body requires only path, line, and column");
            return m_editor.open(
                m_paths, body.value("path").toString(),
                bodyInteger(body, "line"), bodyInteger(body, "column"));
        }, Status::Conflict);
    });
}
