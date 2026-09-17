#include "library/externaltrackloader.h"

#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QtGlobal>
#include <QVector>

#include "mixer/playermanager.h"
#include "sources/soundsourceproxy.h"
#include "util/assert.h"
#include "util/logger.h"

namespace {

const mixxx::Logger kLogger("ExternalTrackLoader");

constexpr int kPollIntervalMs = 500;

} // anonymous namespace

namespace mixxx {

ExternalTrackLoader::ExternalTrackLoader(
        const QString& commandFilePath,
        PlayerManager* pPlayerManager)
        : m_commandFilePath(commandFilePath),
          m_pollTimer(this),
          m_lastSize(-1),
          m_pPlayerManager(pPlayerManager) {
    DEBUG_ASSERT(pPlayerManager);
    m_pollTimer.setInterval(kPollIntervalMs);
    connect(&m_pollTimer, &QTimer::timeout, this, &ExternalTrackLoader::slotCheckForCommand);
    connect(&m_watcher, &QFileSystemWatcher::fileChanged, this, &ExternalTrackLoader::slotCheckForCommand);
}

void ExternalTrackLoader::start() {
    if (!m_watcher.files().isEmpty()) {
        return;
    }
    if (!m_watcher.addPath(m_commandFilePath)) {
        // The file might not exist yet. The poll timer will keep checking.
        kLogger.warning() << "Failed to watch command file" << m_commandFilePath;
    }
    m_pollTimer.start();
}

void ExternalTrackLoader::slotCheckForCommand() {
    QFileInfo info(m_commandFilePath);
    if (!info.exists()) {
        // Keep the watcher armed even if the file gets recreated later.
        if (!m_watcher.files().isEmpty()) {
            m_watcher.removePath(m_commandFilePath);
        }
        return;
    }
    if (info.lastModified() == m_lastSeen && info.size() == m_lastSize) {
        return;
    }
    m_lastSeen = info.lastModified();
    m_lastSize = info.size();

    // Re-arm the watcher: on some platforms (notably Windows) the watcher
    // stops reporting changes after the first one.
    m_watcher.removePath(m_commandFilePath);
    m_watcher.addPath(m_commandFilePath);

    processCommandFile();
}

void ExternalTrackLoader::processCommandFile() {
    QFile file(m_commandFilePath);
    if (!file.open(QIODevice::ReadOnly)) {
        kLogger.warning() << "Cannot open command file" << m_commandFilePath;
        return;
    }
    const QByteArray contents = file.readAll();
    file.close();

    if (contents.trimmed().isEmpty()) {
        return;
    }

    QJsonParseError parseError;
    const QJsonDocument doc = QJsonDocument::fromJson(contents, &parseError);
    if (parseError.error != QJsonParseError::NoError || !doc.isObject()) {
        kLogger.warning() << "Invalid command file content:" << parseError.errorString();
        return;
    }

    const QJsonObject obj = doc.object();

    // Collect (location, group, autoplay) tuples. Supports either a single
    // track ("path" + "group"/"deck") or a list of tracks ("tracks").
    struct Request {
        QString location;
        QString group;
        bool autoplay;
    };
    QVector<Request> requests;

    const QJsonValue tracksValue = obj.value(QStringLiteral("tracks"));
    if (tracksValue.isArray()) {
        const QJsonArray tracks = tracksValue.toArray();
        for (const auto& value : tracks) {
            if (!value.isObject()) {
                continue;
            }
            const QJsonObject track = value.toObject();
            QString location = track.value(QStringLiteral("path")).toString();
            if (location.isEmpty()) {
                location = track.value(QStringLiteral("location")).toString();
            }
            QString group = track.value(QStringLiteral("group")).toString();
            if (group.isEmpty()) {
                const int deck = track.value(QStringLiteral("deck")).toInt(1);
                group = QStringLiteral("[Channel%1]").arg(qMax(1, deck));
            }
            requests.append({location,
                    group,
                    track.value(QStringLiteral("autoplay")).toBool(false)});
        }
    } else {
        QString location = obj.value(QStringLiteral("path")).toString();
        if (location.isEmpty()) {
            location = obj.value(QStringLiteral("location")).toString();
        }
        QString group = obj.value(QStringLiteral("group")).toString();
        if (group.isEmpty()) {
            const int deck = obj.value(QStringLiteral("deck")).toInt(1);
            group = QStringLiteral("[Channel%1]").arg(qMax(1, deck));
        }
        requests.append({location,
                group,
                obj.value(QStringLiteral("autoplay")).toBool(false)});
    }

    for (const auto& request : requests) {
        if (request.location.isEmpty()) {
            kLogger.warning() << "Command file has no 'path'";
            continue;
        }
        const QFileInfo fileInfo(request.location);
        if (!fileInfo.isFile() || !fileInfo.isReadable()) {
            kLogger.warning() << "Ignoring command: file does not exist or is not readable"
                              << request.location;
            continue;
        }
        if (!SoundSourceProxy::isFileNameSupported(request.location)) {
            kLogger.warning() << "Ignoring command: unsupported file type" << request.location;
            continue;
        }
        kLogger.info() << "Loading track from command file:" << request.location
                       << "->" << request.group;
        m_pPlayerManager->slotLoadLocationToPlayer(
                request.location, request.group, request.autoplay);
    }

    // Truncate the file so the command is only applied once.
    if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        file.close();
    }
}

} // namespace mixxx
