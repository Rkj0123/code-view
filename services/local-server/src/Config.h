#pragma once

#include <QByteArray>
#include <QJsonObject>
#include <QStringList>

class RepositoryPath
{
public:
    explicit RepositoryPath(const QString& root);
    ~RepositoryPath();
    RepositoryPath(const RepositoryPath&) = delete;
    RepositoryPath& operator=(const RepositoryPath&) = delete;

    const QString& root() const { return m_root; }
    QString relative(const QString& value) const;
    QString resolve(const QString& value, bool mustExist = true) const;
    QByteArray readFile(const QString& value, qsizetype maxBytes) const;

private:
    QString m_root;
    int m_rootDescriptor = -1;
};

struct LocalConfig
{
    QString root;
    QString path;
    QString language;
    QJsonObject entrypoint;
    bool configExists = false;
    bool includeTests = false;
    QStringList startArgv;
    bool startManual = false;
    QString startDisplay;
    QString startMode = "document";
    QStringList excludePatterns;
    QString modules = "hide";
    QString initialView = "entry-focus";
    QStringList relationships = {"contains", "imports", "calls", "inherits", "constructs", "reads", "writes",
                                 "decorates", "type_uses", "api_calls"};
    QString gitBase = "HEAD";

    static LocalConfig load(const QString& root);
    bool isExcluded(const QString& relativePath) const;
    QJsonObject publicJson() const;
};
