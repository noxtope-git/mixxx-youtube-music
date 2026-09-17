#pragma once

#include <QDateTime>
#include <QFileSystemWatcher>
#include <QObject>
#include <QTimer>

class PlayerManager;

namespace mixxx {

/// Watches a JSON "command file" so that external tools (e.g. the ytmixx
/// bridge) can request loading a local audio file into a deck/sampler without
/// any MIDI/hardware channel.
///
/// The command file has the following JSON schema:
///     {
///         "version": 1,
///         "path": "/absolute/path/to/track.m4a",
///         "group": "[Channel1]",   // or "deck": 1
///         "autoplay": false
///     }
///
/// After a command has been processed the file is truncated so the same
/// request is not applied repeatedly.
///
/// This is a self-contained, opt-in module. Wire it into CoreServices to
/// enable it (see contrib/youtube-music/README.md).
class ExternalTrackLoader : public QObject {
    Q_OBJECT

  public:
    ExternalTrackLoader(
            const QString& commandFilePath,
            PlayerManager* pPlayerManager);
    ~ExternalTrackLoader() override = default;

    void start();

  private slots:
    void slotCheckForCommand();

  private:
    void processCommandFile();

    QString m_commandFilePath;
    QFileSystemWatcher m_watcher;
    QTimer m_pollTimer;
    QDateTime m_lastSeen;
    qint64 m_lastSize;
    PlayerManager* m_pPlayerManager;
};

} // namespace mixxx
