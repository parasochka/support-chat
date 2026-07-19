import { useEffect, useState } from 'react';
import Box from '@mui/material/Box';
import { API_URL, getToken } from '../../httpClient';

// Session-lived cache of the fetched preview object URLs, keyed by photo id.
// The binary is immutable per id, so once fetched a preview is reused across
// re-renders, pagination and filter changes instead of re-downloading — the
// slow-loading complaint. The URLs are intentionally never revoked: they live
// for the tab's lifetime (bounded by how many distinct photos exist).
const photoUrlCache = new Map();

const PhotoPreview = ({ photoId }) => {
  const [src, setSrc] = useState(() => photoUrlCache.get(photoId) || null);
  useEffect(() => {
    const cached = photoUrlCache.get(photoId);
    if (cached) {
      setSrc(cached);
      return undefined;
    }
    let cancelled = false;
    fetch(`${API_URL}/admin/retention/photos/${photoId}/file`, {
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
  }, [photoId]);
  const frame = {
    width: '100%',
    height: 180,
    bgcolor: 'action.hover',
    borderRadius: 1,
    overflow: 'hidden',
  };
  if (!src) return <Box sx={frame} />;
  return (
    <Box sx={frame}>
      <img
        src={src}
        alt=""
        style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
      />
    </Box>
  );
};

export default PhotoPreview;
