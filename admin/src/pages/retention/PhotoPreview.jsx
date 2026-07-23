import { useEffect, useState } from 'react';
import Box from '@mui/material/Box';
import CircularProgress from '@mui/material/CircularProgress';
import Dialog from '@mui/material/Dialog';
import Typography from '@mui/material/Typography';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutlined';
import { API_URL, getToken } from '../../httpClient';
import { t } from '../../i18n';

// Session-lived cache of the fetched preview object URLs, keyed by photo id.
// The binary is immutable per id, so once fetched a preview is reused across
// re-renders, pagination and filter changes instead of re-downloading — the
// slow-loading complaint. The URLs are intentionally never revoked: they live
// for the tab's lifetime (bounded by how many distinct photos exist).
const photoUrlCache = new Map();

// Full video binaries fetched for the lightbox, cached the same way. Kept
// separate from photoUrlCache: a video's grid preview is its poster frame,
// the lightbox plays the actual (normalized, <=50 MB) MP4.
const videoUrlCache = new Map();

// The file endpoint requires the Bearer header, so a plain <img src>/<video
// src> can't reach it — everything loads as an authorized blob.
const fetchMediaUrl = (photoId, { poster }) =>
  fetch(`${API_URL}/admin/retention/photos/${photoId}/file${poster ? '?poster=1' : ''}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
    .then((r) => (r.ok ? r.blob() : null))
    .then((blob) => (blob ? URL.createObjectURL(blob) : null));

// A video row previews via its extracted poster frame (?poster=1) — a still
// image, not the multi-MB video binary — with a play badge overlaid. Clicking
// a preview opens a minimal lightbox dialog: the full-size photo, or the
// playable video (fetched on first open).
const PhotoPreview = ({ photoId, mediaType }) => {
  const isVideo = mediaType === 'video';
  const [src, setSrc] = useState(() => photoUrlCache.get(photoId) || null);
  const [open, setOpen] = useState(false);
  const [videoSrc, setVideoSrc] = useState(() => videoUrlCache.get(photoId) || null);
  useEffect(() => {
    const cached = photoUrlCache.get(photoId);
    if (cached) {
      setSrc(cached);
      return undefined;
    }
    let cancelled = false;
    fetchMediaUrl(photoId, { poster: isVideo })
      .then((url) => {
        if (url && !cancelled) {
          photoUrlCache.set(photoId, url);
          setSrc(url);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [photoId, isVideo]);
  // The video binary is fetched lazily, on the first lightbox open — the grid
  // must never pull multi-MB files for rows the operator doesn't inspect.
  useEffect(() => {
    if (!open || !isVideo || videoSrc) return undefined;
    const cached = videoUrlCache.get(photoId);
    if (cached) {
      setVideoSrc(cached);
      return undefined;
    }
    let cancelled = false;
    fetchMediaUrl(photoId, { poster: false })
      .then((url) => {
        if (url && !cancelled) {
          videoUrlCache.set(photoId, url);
          setVideoSrc(url);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [open, isVideo, videoSrc, photoId]);
  const frame = {
    width: '100%',
    height: 180,
    bgcolor: 'action.hover',
    borderRadius: 1,
    overflow: 'hidden',
    position: 'relative',
    cursor: src ? 'pointer' : 'default',
  };
  const badge = isVideo ? (
    <PlayCircleOutlineIcon
      sx={{
        position: 'absolute',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        fontSize: 48,
        color: 'rgba(255,255,255,0.85)',
        filter: 'drop-shadow(0 0 4px rgba(0,0,0,0.6))',
        pointerEvents: 'none',
      }}
    />
  ) : null;
  // Minimal lightbox: dark dialog, media only, backdrop/Esc closes it.
  const lightbox = (
    <Dialog
      open={open}
      onClose={() => setOpen(false)}
      maxWidth={false}
      PaperProps={{ sx: { bgcolor: '#000', boxShadow: 'none', borderRadius: 1 } }}
    >
      {isVideo ? (
        videoSrc ? (
          <video
            src={videoSrc}
            controls
            autoPlay
            style={{ maxWidth: '92vw', maxHeight: '88vh', display: 'block' }}
          />
        ) : (
          <Box sx={{ p: 5, display: 'flex', alignItems: 'center', gap: 2 }}>
            <CircularProgress size={22} sx={{ color: '#fff' }} />
            <Typography variant="body2" sx={{ color: '#fff' }}>
              {t('Loading video…')}
            </Typography>
          </Box>
        )
      ) : (
        <img
          src={src}
          alt=""
          style={{ maxWidth: '92vw', maxHeight: '88vh', display: 'block' }}
        />
      )}
    </Dialog>
  );
  if (!src) return <Box sx={frame}>{badge}</Box>;
  return (
    <>
      <Box sx={frame} onClick={() => setOpen(true)}>
        <img
          src={src}
          alt=""
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
        {badge}
      </Box>
      {lightbox}
    </>
  );
};

export default PhotoPreview;
