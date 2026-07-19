import { usePermissions } from 'react-admin';

// Managers are read-only (cosmetic — the server is authoritative).
export const useReadOnly = () => {
  const { permissions } = usePermissions();
  return permissions !== 'admin';
};
