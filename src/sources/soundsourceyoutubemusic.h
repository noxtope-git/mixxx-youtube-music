#pragma once

#include "sources/soundsourceprovider.h"

namespace mixxx {

/// Lazily resolves and decodes a YouTube Music track.
///
/// The "file" referenced by the URL is a tiny `.ytmusic` sidecar: either an
/// empty file whose name is the video ID (e.g. `dQw4w9WgXcQ.ytmusic`) or a
/// text file containing the video ID or a YouTube/YouTube Music URL.
///
/// On first open the audio is downloaded to a local cache (using the `yt-dlp`
/// command line tool) and decoding is delegated to the FFmpeg sound source.
/// Because decoding is seekable and happens off the real-time audio thread,
/// the DJ features (loops, hotcues, keylock, ...) keep working.
///
/// This module is intentionally opt-in and does not ship with Mixxx by
/// default. See contrib/youtube-music/README.md for build instructions.
class SoundSourceYouTubeMusic final : public SoundSource {
  public:
    explicit SoundSourceYouTubeMusic(const QUrl& url);
    ~SoundSourceYouTubeMusic() override;

    void close() override;

    /// Returns true if `url` points to a YouTube or YouTube Music video.
    static bool isYoutubeUrl(const QUrl& url);

    /// Returns the 11-character video ID from a YouTube URL, or an empty
    /// string if the URL is not recognized.
    static QString videoIdFromUrl(const QUrl& url);

    /// Creates a `.ytmusic` sidecar file in the cache directory that can be
    /// loaded as a track. Returns the sidecar's absolute path, or an empty
    /// string on failure.
    static QString createSidecarForUrl(const QUrl& url);

  protected:
    ReadableSampleFrames readSampleFramesClamped(
            const WritableSampleFrames& sampleFrames) override;

    OpenResult tryOpen(
            OpenMode mode,
            const OpenParams& params) override;

  private:
    QString resolveVideoId() const;
    static QString cacheDirPath();
    bool downloadToCache(const QString& videoId, QString* pFilePath);

    SoundSourcePointer m_pDelegate;
};

class SoundSourceProviderYouTubeMusic final : public SoundSourceProvider {
  public:
    static const QString kDisplayName;

    QString getDisplayName() const override {
        return kDisplayName;
    }

    QStringList getSupportedFileTypes() const override;

    SoundSourcePointer newSoundSource(const QUrl& url) override {
        return newSoundSourceFromUrl<SoundSourceYouTubeMusic>(url);
    }
};

} // namespace mixxx
