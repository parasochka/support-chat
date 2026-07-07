import { useLocation, useNavigate } from 'react-router-dom';
import Tabs from '@mui/material/Tabs';
import Tab from '@mui/material/Tab';

/**
 * Route-linked tab strip for surfaces that are really one page split across
 * react-admin routes (Knowledge base <-> KB variables). The active tab is the
 * route whose path prefixes the current location.
 */
const RouteTabs = ({ tabs }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const active =
    [...tabs]
      .sort((a, b) => b.path.length - a.path.length)
      .find((t) => location.pathname.startsWith(t.path))?.path || tabs[0].path;

  return (
    <Tabs
      value={active}
      onChange={(e, v) => navigate(v)}
      sx={{ borderBottom: 1, borderColor: 'divider', mb: 1 }}
      variant="scrollable"
      allowScrollButtonsMobile
    >
      {tabs.map((t) => (
        <Tab key={t.path} value={t.path} label={t.label} />
      ))}
    </Tabs>
  );
};

export default RouteTabs;
