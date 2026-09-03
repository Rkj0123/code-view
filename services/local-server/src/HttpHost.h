#pragma once

#include "Runtime.h"

#include <QHttpServer>
#include <QTcpServer>
#include <memory>

class HttpHost: public QObject
{
    Q_OBJECT

public:
    HttpHost(
        HostState* state,
        AnalyzerRunner* analyzer,
        StartCommandController* commands,
        EditorLauncher editor,
        QString webRoot,
        QObject* parent = nullptr);

    bool listen(quint16 port = 0);
    QString listenError() const { return m_listenError; }
    quint16 port() const { return m_tcp.serverPort(); }
    QString token() const { return QString::fromLatin1(m_token); }
    QString origin() const;
    QString launchUrl() const;

private:
    using Status = QHttpServerResponse::StatusCode;

    void addRoutes();
    QHttpServerResponse json(QJsonObject value, Status status = Status::Ok) const;
    QHttpServerResponse secured(QHttpServerResponse response) const;
    QHttpServerResponse failure(const QString& message, Status status) const;
    QHttpServerResponse guard(const QHttpServerRequest& request, bool requireBearer) const;
    bool secureEquals(const QByteArray& left, const QByteArray& right) const;
    bool isGuardFailure(const QHttpServerResponse& response) const;
    QHttpServerResponse staticFile(const QString& relative) const;
    QHttpServerResponse spa(const QHttpServerRequest& request) const;
    QJsonObject wrapGraph(const StateView& state, QJsonObject graph, QJsonValue comparison = QJsonValue()) const;

    HostState* m_state;
    AnalyzerRunner* m_analyzer;
    StartCommandController* m_commands;
    EditorLauncher m_editor;
    RepositoryPath m_paths;
    std::unique_ptr<RepositoryPath> m_assets;
    QByteArray m_token;
    std::unique_ptr<GraphComparator> m_comparator;
    QHttpServer m_http;
    QTcpServer m_tcp;
    QString m_listenError;
};
