#pragma once

#include <QProcess>
#include <QTimer>

#include "library/baseexternallibraryfeature.h"
#include "library/baseexternaltrackmodel.h"
#include "util/parented_ptr.h"

class BaseTrackCache;

// A track model backed by the "youtube_music_library" temporary SQL table.
// Overrides `search()` so that typing in Mixxx's library search box triggers a
// live YouTube Music query (via yt-dlp) and repopulates the table.
class YouTubeMusicTrackModel : public BaseExternalTrackModel {
    Q_OBJECT
  public:
    YouTubeMusicTrackModel(
            QObject* parent,
            TrackCollectionManager* pTrackCollectionManager,
            const QString& trackTable,
            QSharedPointer<BaseTrackCache> trackSource);
    ~YouTubeMusicTrackModel() override;

    void search(const QString& searchText) override;

  private slots:
    void slotRunSearch();
    void slotSearchFinished(int exitCode, QProcess::ExitStatus exitStatus);

  private:
    void clearTable();
    void insertResult(const QString& videoId,
            const QString& title,
            const QString& artist,
            int durationSecs);

    QString m_pendingSearch;
    QTimer m_debounceTimer;
    QProcess* m_pProcess;
};

class YouTubeMusicFeature : public BaseExternalLibraryFeature {
    Q_OBJECT
  public:
    YouTubeMusicFeature(Library* pLibrary, UserSettingsPointer pConfig);
    ~YouTubeMusicFeature() override;

    QVariant title() override;
    TreeItemModel* sidebarModel() const override;

  public slots:
    void activate() override;

  private:
    void createLibraryTable();

    YouTubeMusicTrackModel* m_pTrackModel;
    QSharedPointer<BaseTrackCache> m_trackSource;
    parented_ptr<TreeItemModel> m_pSidebarModel;
    QString m_title;
};
