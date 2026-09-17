#include "sources/soundsourceyoutubemusic.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QProcess>
#include <QRegularExpression>
#include <QStandardPaths>

#include "util/logger.h"

#ifdef __FFMPEG__
#include "sources/soundsourceffmpeg.h"
#endif

namespace {

const mixxx::Logger kLogger("SoundSourceYouTubeMusic");

constexpr int kDownloadTimeoutMs = 10 * 60 * 1000;

const QRegularExpression kUrlIdRegex(QStringLiteral(
        "(?:youtube\\.com|youtu\\.be|music\\.youtube\\.com)/.*[?&]v=([A-Za-z0-9_-]{11})"));
const QRegularExpression kBareIdRegex(QStringLiteral("^[A-Za-z0-9_-]{11}$"));

} // anonymous namespace

namespace mixxx {

const QString SoundSourceProviderYouTubeMusic::kDisplayName =
        QStringLiteral("YouTube Music");

SoundSourceYouTubeMusic::SoundSourceYouTubeMusic(const QUrl& url)
        : SoundSource(url) {
}

SoundSourceYouTubeMusic::~SoundSourceYouTubeMusic() = default;

void SoundSourceYouTubeMusic::close() {
    if (m_pDelegate) {
        m_pDelegate->close();
    }
}

QString SoundSourceYouTubeMusic::resolveVideoId() const {
    const QString localFile = getUrl().toLocalFile();

    QString content;
    QFile file(localFile);
    if (file.open(QIODevice::ReadOnly)) {
        content = QString::fromUtf8(file.readAll()).trimmed();
        file.close();
    }

    const QRegularExpressionMatch urlMatch = kUrlIdRegex.match(content);
    if (urlMatch.hasMatch()) {
        return urlMatch.captured(1);
    }
    if (kBareIdRegex.match(content).hasMatch()) {
        return content;
    }

    // Fall back to the file name without its extension.
    return QFileInfo(localFile).completeBaseName();
}

// static
QString SoundSourceYouTubeMusic::videoIdFromUrl(const QUrl& url) {
    const QRegularExpressionMatch m = kUrlIdRegex.match(url.toString());
    if (m.hasMatch()) {
        return m.captured(1);
    }
    return QString();
}

// static
bool SoundSourceYouTubeMusic::isYoutubeUrl(const QUrl& url) {
    return !videoIdFromUrl(url).isEmpty();
}

// static
QString SoundSourceYouTubeMusic::createSidecarForUrl(const QUrl& url) {
    const QString videoId = videoIdFromUrl(url);
    if (videoId.isEmpty()) {
        return QString();
    }
    const QString dir = cacheDirPath();
    if (!QDir().mkpath(dir)) {
        return QString();
    }
    const QString sidecar = QDir(dir).filePath(videoId + QStringLiteral(".ytmusic"));
    QFile file(sidecar);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        return QString();
    }
    file.write(url.toString().toUtf8());
    file.close();
    return sidecar;
}

QString SoundSourceYouTubeMusic::cacheDirPath() {
    const QString env = qEnvironmentVariable("MIXXX_YOUTUBE_CACHE");
    if (!env.isEmpty()) {
        return env;
    }
    return QDir(QStandardPaths::writableLocation(QStandardPaths::CacheLocation))
            .filePath(QStringLiteral("youtube"));
}

bool SoundSourceYouTubeMusic::downloadToCache(
        const QString& videoId,
        QString* pFilePath) {
    const QString cacheDir = cacheDirPath();
    if (!QDir().mkpath(cacheDir)) {
        kLogger.warning() << "Cannot create cache directory" << cacheDir;
        return false;
    }

    QString exe = QStandardPaths::findExecutable(QStringLiteral("yt-dlp"));
    if (exe.isEmpty()) {
        exe = QStandardPaths::findExecutable(QStringLiteral("yt-dlp.exe"));
    }
    if (exe.isEmpty()) {
        kLogger.warning() << "yt-dlp not found in PATH";
        return false;
    }

    const QString outTmpl =
            QDir(cacheDir).filePath(QStringLiteral("%(id)s.%(ext)s"));
    const QString url =
            QStringLiteral("https://music.youtube.com/watch?v=%1").arg(videoId);

    QStringList args;
    args << QStringLiteral("-f") << QStringLiteral("bestaudio/best")
         << QStringLiteral("--no-playlist")
         << QStringLiteral("--no-progress")
         << QStringLiteral("--print") << QStringLiteral("after_move:filepath")
         << QStringLiteral("-o") << outTmpl
         << url;

    QProcess proc;
    proc.setProcessChannelMode(QProcess::SeparateChannels);
    proc.start(exe, args);
    if (!proc.waitForStarted(5000)) {
        kLogger.warning() << "Failed to start yt-dlp";
        return false;
    }
    if (!proc.waitForFinished(kDownloadTimeoutMs)) {
        proc.kill();
        proc.waitForFinished();
        kLogger.warning() << "yt-dlp download timed out";
        return false;
    }
    if (proc.exitStatus() != QProcess::NormalExit || proc.exitCode() != 0) {
        kLogger.warning() << "yt-dlp failed:"
                          << QString::fromUtf8(proc.readAllStandardError());
        return false;
    }

    const QString output =
            QString::fromUtf8(proc.readAllStandardOutput()).trimmed();
    const QStringList lines = output.split(QLatin1Char('\n'), Qt::SkipEmptyParts);
    if (lines.isEmpty()) {
        kLogger.warning() << "yt-dlp did not print a file path";
        return false;
    }
    const QString path = lines.last().trimmed();
    if (!QFileInfo::exists(path)) {
        kLogger.warning() << "Downloaded file not found:" << path;
        return false;
    }
    *pFilePath = path;
    return true;
}

SoundSourceYouTubeMusic::OpenResult SoundSourceYouTubeMusic::tryOpen(
        OpenMode mode,
        const OpenParams& params) {
    const QString videoId = resolveVideoId();
    if (videoId.isEmpty()) {
        return OpenResult::Aborted;
    }

    QString cachedFile;
    if (!downloadToCache(videoId, &cachedFile)) {
        return OpenResult::Failed;
    }

#ifdef __FFMPEG__
    m_pDelegate =
            std::make_shared<SoundSourceFFmpeg>(QUrl::fromLocalFile(cachedFile));
    const OpenResult result = m_pDelegate->open(mode, params);
    if (result != OpenResult::Succeeded) {
        m_pDelegate.reset();
        return result;
    }

    const auto& signalInfo = m_pDelegate->getSignalInfo();
    initChannelCountOnce(signalInfo.getChannelCount());
    initSampleRateOnce(signalInfo.getSampleRate());
    initBitrateOnce(m_pDelegate->getBitrate());
    initFrameIndexRangeOnce(m_pDelegate->frameIndexRange());
    return OpenResult::Succeeded;
#else
    Q_UNUSED(mode);
    Q_UNUSED(params);
    kLogger.warning() << "SoundSourceYouTubeMusic requires FFmpeg support";
    return OpenResult::Failed;
#endif
}

ReadableSampleFrames SoundSourceYouTubeMusic::readSampleFramesClamped(
        const WritableSampleFrames& sampleFrames) {
    if (!m_pDelegate) {
        return ReadableSampleFrames();
    }
    return m_pDelegate->readSampleFrames(sampleFrames);
}

QStringList SoundSourceProviderYouTubeMusic::getSupportedFileTypes() const {
    return {QStringLiteral("ytmusic")};
}

} // namespace mixxx
