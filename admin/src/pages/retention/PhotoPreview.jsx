import { useEffect, useState } from 'react';
import Box from '@mui/material/Box';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutlined';
import { API_URL, getToken } from '../../httpClient';

// Session-lived cache of the fetched preview object URLs, keyed by photo id.
// The binary is immutable per id, so once fetched a preview is reused across
// re-renders, pagination and filter changes instead of re-downloading — the
// slow-loading complaint. The URLs are intentionally never revoked: they live
// for the tab's lifetime (bounded by how many distinct photos exist).
const photoUrlCache = new Map();

// A video row previews via its extracted poster frame (?poster=1) — a still
// image, not the multi-MB video binary — with a play badge overlaid.
const PhotoPreview = ({ photoId, mediaType }) => {
  const isVideo = mediaType === 'video';
  const [src, setSrc] = useState(() => photoUrlCache.get(photoId) || null);
  useEffect(() => {
    const cached = photoUrlCache.get(photoId);
    if (cached) {
      setSrc(cached);
      return undefined;
    }
    let cancelled = false;
    const suffix = isVideo ? '?poster=1' : '';
    fetch(`${API_URL}/admin/retention/photos/${photoId}/file${suffix}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    })
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (blob && !cancelled) {
          const url = URL.createObjectURL(blob);
          photoUrlCache.set(photoId, url);
          setSrc(url);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [photoId, isVideo]);
  const frame = {
    width: '100%',
    height: 180,
    bgcolor: 'action.hover',
    borderRadius: 1,
    overflow: 'hidden',
    position: 'relative',
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
  if (!src) return <Box sx={frame}>{badge}</Box>;
  return (
    <Box sx={frame}>
      <img
        src={src}
        alt=""
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
      {badge}
    </Box>
  );
};

export default PhotoPreview;
