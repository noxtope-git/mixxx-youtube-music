#include "library/youtubemusic/youtubemusicfeature.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QSqlQuery>
#include <QStandardPaths>
#include <QUrl>

#include "library/basetrackcache.h"
#include "library/baseexternaltrackmodel.h"
#include "library/library.h"
#include "library/trackcollectionmanager.h"
#include "library/treeitem.h"
#include "library/treeitemmodel.h"
#include "sources/soundsourceyoutubemusic.h"
#include "util/logger.h"

namespace {

const mixxx::Logger kLogger("YouTubeMusicFeature");

const QString kTableName = QStringLiteral("youtube_music_library");
const int kSearchDebounceMs = 400;
const int kMaxResults = 10;

} // anonymous namespace

// ---------------------------------------------------------------------------
// YouTubeMusicTrackModel
// ---------------------------------------------------------------------------

YouTubeMusicTrackModel::YouTubeMusicTrackModel(
        QObject* parent,
        TrackCollectionManager* pTrackCollectionManager,
        const QString& trackTable,
        QSharedPointer<BaseTrackCache> trackSource)
        : BaseExternalTrackModel(parent,
                  pTrackCollectionManager,
                  "mixxx.db.model.youtubemusic",
                  trackTable,
                  trackSource),
          m_pProcess(new QProcess(this)) {
    m_debounceTimer.setSingleShot(true);
    m_debounceTimer.setInterval(kSearchDebounceMs);
    connect(&m_debounceTimer, &QTimer::timeout, this, &YouTubeMusicTrackModel::slotRunSearch);

    m_pProcess->setProcessChannelMode(QProcess::SeparateChannels);
    connect(m_pProcess,
            QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this,
            &YouTubeMusicTrackModel::slotSearchFinished);
}

YouTubeMusicTrackModel::~YouTubeMusicTrackModel() {
    if (m_pProcess->state() != QProcess::NotRunning) {
        m_pProcess->kill();
        m_pProcess->waitForFinished(1000);
    }
}

void YouTubeMusicTrackModel::search(const QString& searchText) {
    // Debounce: yt-dlp is invoked only after the user pauses typing.
    m_pendingSearch = searchText.trimmed();
    if (m_pendingSearch.isEmpty()) {
        clearTable();
        select();
        return;
    }
    m_debounceTimer.start();
}

void YouTubeMusicTrackModel::slotRunSearch() {
    QString exe = QStandardPaths::findExecutable(QStringLiteral("yt-dlp"));
    if (exe.isEmpty()) {
        exe = QStandardPaths::findExecutable(QStringLiteral("yt-dlp.exe"));
    }
    if (exe.isEmpty()) {
        kLogger.warning() << "yt-dlp not found in PATH";
        return;
    }

    const QString query = QStringLiteral("ytsearch%1:%2")
                                  .arg(kMaxResults)
                                  .arg(m_pendingSearch);
    m_pProcess->start(exe,
            {QStringLiteral("--flat-playlist"),
                    QStringLiteral("--dump-json"),
                    query});
}

void YouTubeMusicTrackModel::slotSearchFinished(
        int exitCode, QProcess::ExitStatus exitStatus) {
    if (exitStatus != QProcess::NormalExit || exitCode != 0) {
        kLogger.warning() << "yt-dlp search failed";
        return;
    }

    clearTable();

    const QString output = QString::fromUtf8(m_pProcess->readAllStandardOutput());
    const QStringList lines = output.split(QLatin1Char('\n'), Qt::SkipEmptyParts);
    for (const QString& line : lines) {
        QJsonParseError err;
        const QJsonDocument doc = QJsonDocument::fromJson(line.toUtf8(), &err);
        if (err.error != QJsonParseError::NoError || !doc.isObject()) {
            continue;
        }
        const QJsonObject obj = doc.object();
        const QString id = obj.value(QStringLiteral("id")).toString();
        if (id.isEmpty()) {
            continue;
        }
        const QString title = obj.value(QStringLiteral("title")).toString();
        const QString artist = obj.value(QStringLiteral("channel")).toString(
                obj.value(QStringLiteral("uploader")).toString());
        const int duration = obj.value(QStringLiteral("duration")).toInt();
        insertResult(id, title, artist, duration);
    }

    // Disable the SQL search filter: the table already holds the matching
    // results, so applying the raw query again would double-filter them.
    setSearch(QString());
    select();
}

void YouTubeMusicTrackModel::clearTable() {
    QSqlDatabase db = m_pTrackCollectionManager->internalCollection()->database();
    QSqlQuery query(db);
    if (!query.exec(QStringLiteral("DELETE FROM %1").arg(kTableName))) {
        kLogger.warning() << "Failed to clear YouTube Music table";
    }
}

void YouTubeMusicTrackModel::insertResult(
        const QString& videoId,
        const QString& title,
        const QString& artist,
        int durationSecs) {
    // A .ytmusic sidecar makes the track loadable via the YouTube Music sound
    // source (which downloads + decodes on demand).
    const QString sidecar = mixxx::SoundSourceYouTubeMusic::createSidecarForUrl(
            QUrl(QStringLiteral("https://music.youtube.com/watch?v=%1").arg(videoId)));
    if (sidecar.isEmpty()) {
        return;
    }

    QSqlQuery query(m_pTrackCollectionManager->internalCollection()->database());
    query.prepare(QStringLiteral(
            "INSERT INTO %1 (artist, title, album, year, genre, tracknumber, "
            "location, comment, rating, duration, bitrate, bpm) "
            "VALUES (:artist, :title, '', 0, '', '', :location, '', 0, :duration, '', 0)")
                          .arg(kTableName));
    query.bindValue(QStringLiteral(":artist"), artist);
    query.bindValue(QStringLiteral(":title"), title);
    query.bindValue(QStringLiteral(":location"), sidecar);
    query.bindValue(QStringLiteral(":duration"), durationSecs);
    if (!query.exec()) {
        kLogger.warning() << "Failed to insert YouTube Music result";
    }
}

// ---------------------------------------------------------------------------
// YouTubeMusicFeature
// ---------------------------------------------------------------------------

YouTubeMusicFeature::YouTubeMusicFeature(
        Library* pLibrary, UserSettingsPointer pConfig)
        : BaseExternalLibraryFeature(pLibrary, pConfig, QStringLiteral("ic_library_music")),
          m_pSidebarModel(make_parented<TreeItemModel>(this)) {
    createLibraryTable();

    const QStringList columns = {
            "id", "artist", "title", "album", "year", "genre",
            "tracknumber", "location", "comment", "rating",
            "duration", "bitrate", "bpm"};
    const QStringList searchColumns = {"artist", "album", "title", "genre"};

    m_trackSource = QSharedPointer<BaseTrackCache>::create(
            m_pTrackCollection,
            kTableName,
            QStringLiteral("id"),
            columns,
            searchColumns,
            false);

    m_pTrackModel = new YouTubeMusicTrackModel(this,
            pLibrary->trackCollectionManager(),
            kTableName,
            m_trackSource);

    m_title = tr("YouTube Music");

    m_pSidebarModel->setRootItem(TreeItem::newRoot(this));
    m_pTrackModel->setSearch(QString()); // enable the library search box
}

YouTubeMusicFeature::~YouTubeMusicFeature() = default;

QVariant YouTubeMusicFeature::title() {
    return m_title;
}

TreeItemModel* YouTubeMusicFeature::sidebarModel() const {
    return m_pSidebarModel;
}

void YouTubeMusicFeature::activate() {
    emit showTrackModel(m_pTrackModel);
    emit enableCoverArtDisplay(false);
}

void YouTubeMusicFeature::createLibraryTable() {
    QSqlDatabase db = m_pLibrary->trackCollectionManager()
                              ->internalCollection()
                              ->database();
    QSqlQuery query(db);
    query.prepare(QStringLiteral(
            "CREATE TABLE IF NOT EXISTS %1 ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " artist TEXT,"
            " title TEXT,"
            " album TEXT,"
            " year INTEGER,"
            " genre TEXT,"
            " tracknumber TEXT,"
            " location TEXT UNIQUE,"
            " comment TEXT,"
            " duration INTEGER,"
            " bitrate TEXT,"
            " bpm FLOAT,"
            " key TEXT,"
            " rating INTEGER"
            ");")
                          .arg(kTableName));
    if (!query.exec()) {
        kLogger.warning() << "Failed to create YouTube Music table";
    }
}
