import { useState } from 'react';
import {
  Admin,
  CustomRoutes,
  Layout,
  Menu,
  Resource,
  defaultDarkTheme,
  defaultLightTheme,
  usePermissions,
} from 'react-admin';
import { Navigate, Route, useLocation, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Collapse from '@mui/material/Collapse';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import ForumIcon from '@mui/icons-material/Forum';
import VpnKeyIcon from '@mui/icons-material/VpnKey';
import InsightsIcon from '@mui/icons-material/Insights';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';
import LinkIcon from '@mui/icons-material/Link';
import PeopleIcon from '@mui/icons-material/People';
import PhotoLibraryIcon from '@mui/icons-material/PhotoLibrary';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import SettingsIcon from '@mui/icons-material/Settings';
import SupportAgentIcon from '@mui/icons-material/SupportAgent';
import TelegramIcon from '@mui/icons-material/Telegram';
import TranslateIcon from '@mui/icons-material/Translate';
import TuneIcon from '@mui/icons-material/Tune';

import authProvider from './authProvider';
import dataProvider from './dataProvider';
import buildStore from './store';
import LoginPage from './pages/LoginPage';
import ApiKeys from './pages/ApiKeys';
import Dashboard from './dashboard/Dashboard';
import ScopeAppBar from './layout/ScopeAppBar';
import { ConversationList, ConversationShow } from './resources/Conversations';
import { EscalationList } from './resources/Escalations';
import { KbCreate, KbEdit, KbList } from './resources/KnowledgeBase';
import { KbVariableEdit, KbVariableList } from './resources/KbVariables';
import { UserCreate, UserEdit, UserList } from './resources/Users';
import Prompt from './pages/Prompt';
import Retention from './pages/Retention';
import RetentionAgent from './pages/RetentionAgent';
import Settings from './pages/Settings';
import SiteMap from './pages/SiteMap';
import Structure from './pages/Structure';
import Translations from './pages/Translations';

// One store instance for the app: a localStorage store whose reset() preserves
// the theme + sidebar preference across logout (see ./store).
const store = buildStore();

// Collapsed/expanded state of the sidebar sections persists across reloads so
// the operator's layout is remembered (default: expanded).
const OPEN_KEY = 'admin_menu_sections';
const loadOpen = () => {
  try {
    return JSON.parse(localStorage.getItem(OPEN_KEY)) || {};
  } catch {
    return {};
  }
};
const saveOpen = (state) => localStorage.setItem(OPEN_KEY, JSON.stringify(state));

const CollapsibleSection = ({ id, label, children }) => {
  const [open, setOpen] = useState(() => loadOpen()[id] !== false);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    saveOpen({ ...loadOpen(), [id]: next });
  };
  return (
    <>
      <ListItemButton onClick={toggle} sx={{ px: 2, py: { xs: 1, md: 0.5 } }}>
        <ListItemText
          primary={label}
          slotProps={{
            primary: { variant: 'overline', color: 'text.secondary' },
          }}
        />
        {open ? <ExpandLess fontSize="small" /> : <ExpandMore fontSize="small" />}
      </ListItemButton>
      <Collapse in={open} timeout="auto" unmountOnExit>
        {/* Every child of a section is a nested entry — indent uniformly so the
            hierarchy reads at a glance (matches the Retention sub-items). */}
        <Box sx={{ '& .MuiMenuItem-root, & .MuiListItemButton-root': { pl: 4 } }}>
          {children}
        </Box>
      </Collapse>
    </>
  );
};

// Two retention tabs are bundled under a parent's sidebar entry (like the
// Support "Prompt" page): the Setup guide lives under Telegram config, and the
// Prompt variables under Prompt. So those parent entries stay highlighted while
// their sub-tab is active.
const RETENTION_TAB_PARENT = { guide: 'config', variables: 'prompt' };

// A retention sub-tab as its own sidebar entry: navigates to /retention?tab=…
// and highlights when that tab (or one bundled under it) is the active one (the
// page reads ?tab=).
const RetentionSubItem = ({ tab, label, icon }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const current = new URLSearchParams(location.search).get('tab') || 'config';
  const active =
    location.pathname.startsWith('/retention') &&
    (RETENTION_TAB_PARENT[current] || current) === tab;
  return (
    <ListItemButton
      selected={active}
      onClick={() => navigate(`/retention?tab=${tab}`)}
      sx={{ pl: 4, py: { xs: 1, md: 0.4 } }}
    >
      <ListItemIcon sx={{ minWidth: 34 }}>{icon}</ListItemIcon>
      {/* No typography override: the Menu-level rule below pins one font size
          for every entry, so sub-items match the resource/custom items. */}
      <ListItemText primary={label} />
    </ListItemButton>
  );
};

/**
 * The sidebar in three collapsible sections: the support-chat surface, the
 * Telegram retention bot (whose sub-tabs are exposed as sub-menu entries), and
 * system-wide management. Section open/closed state is remembered. KB variables
 * ride inside the Knowledge base page (a tab there), so they get no menu item.
 */
const AppMenu = () => {
  // API keys are credentials — the entry is admin-only (the server refuses
  // managers anyway; hiding it mirrors that, like the page itself does).
  const { permissions } = usePermissions();
  return (
    // Sidebar entries come from three different sources whose default typography
    // differs, so they must be normalized to ONE look. The catch: RA's
    // Menu.ResourceItem / Menu.Item (MenuItemLink) render the label as a bare
    // <Typography variant="inherit"> directly inside .MuiMenuItem-root — NOT a
    // ListItemText — while the retention sub-items (ListItemButton) render a
    // .MuiListItemText-primary. A rule that targets only one of those leaves the
    // column two-toned. So every rule below is written to cover BOTH the MenuItem
    // label and the ListItemText label. Section headers keep their overline
    // style (excluded via :not()).
    <Menu
      sx={{
        '& .MuiListItemIcon-root .MuiSvgIcon-root': { fontSize: 20 },
        // One icon size + inactive colour for every entry source.
        '& .MuiListItemIcon-root': { minWidth: 34, color: 'text.secondary' },
        // One inactive label size + colour. .MuiMenuItem-root covers the RA
        // links (label is an inheriting Typography), .MuiListItemText-primary
        // covers the retention sub-items; both to text.secondary so the whole
        // inactive column is a single tone (RA's default, matching Dashboard).
        '& .MuiMenuItem-root': { fontSize: '0.875rem', color: 'text.secondary' },
        '& .MuiListItemText-primary:not(.MuiTypography-overline)': {
          fontSize: '0.875rem',
          color: 'text.secondary',
        },
        // Active/selected entry — same accent for every source. RA marks the
        // active link with .RaMenuItemLink-active on the MenuItem (its inheriting
        // label picks up the colour); the retention sub-item uses .Mui-selected
        // on the ListItemButton (colour pinned on its ListItemText). Cover both,
        // label + icon, so the highlight reads identically everywhere.
        '& .RaMenuItemLink-active': { color: 'primary.main', fontWeight: 600 },
        '& .Mui-selected .MuiListItemText-primary:not(.MuiTypography-overline)': {
          color: 'primary.main',
          fontWeight: 600,
        },
        '& .RaMenuItemLink-active .MuiListItemIcon-root, & .Mui-selected .MuiListItemIcon-root':
          { color: 'primary.main' },
      }}
    >
      <Menu.DashboardItem primaryText="Dashboard" />

      <CollapsibleSection id="support" label="Support chat">
        <Menu.ResourceItem name="sessions" />
        <Menu.ResourceItem name="unresolved" />
        <Menu.ResourceItem name="kb" />
        <Menu.Item to="/site-map" primaryText="Site map" leftIcon={<LinkIcon />} />
        <Menu.Item to="/prompt" primaryText="Prompt" leftIcon={<TuneIcon />} />
        <Menu.Item
          to="/translations"
          primaryText="Translations"
          leftIcon={<TranslateIcon />}
        />
        {/* The combined dashboard narrowed to the support block. */}
        <Menu.Item
          to="/?module=support"
          primaryText="Analytics"
          leftIcon={<InsightsIcon />}
        />
      </CollapsibleSection>

      <CollapsibleSection id="retention" label="Telegram · Retention">
        {/* Setup guide is a sub-tab of Telegram config; Prompt variables a
            sub-tab of Prompt — so neither gets its own sidebar entry. */}
        <RetentionSubItem tab="config" label="Telegram config" icon={<TelegramIcon fontSize="small" />} />
        <RetentionSubItem tab="kb" label="Retention KB" icon={<LibraryBooksIcon fontSize="small" />} />
        <RetentionSubItem tab="prompt" label="Prompt" icon={<TuneIcon fontSize="small" />} />
        <RetentionSubItem tab="photos" label="Media" icon={<PhotoLibraryIcon fontSize="small" />} />
        <RetentionSubItem tab="managers" label="Managers" icon={<SupportAgentIcon fontSize="small" />} />
        {/* The proactive agent — the event-driven regime that writes first;
            a full page of its own (enable + tune in Settings → Retention bot). */}
        <Menu.Item
          to="/retention-agent"
          primaryText="Proactive agent"
          leftIcon={<SmartToyIcon />}
        />
        <RetentionSubItem tab="chats" label="Conversations" icon={<ForumIcon fontSize="small" />} />
        <RetentionSubItem tab="analytics" label="Analytics" icon={<InsightsIcon fontSize="small" />} />
      </CollapsibleSection>

      <CollapsibleSection id="system" label="System">
        <Menu.Item to="/structure" primaryText="Structure" leftIcon={<AccountTreeIcon />} />
        <Menu.Item to="/settings" primaryText="Settings" leftIcon={<SettingsIcon />} />
        {/* User management is admin-only server-side (403 for managers) —
            hide the entry instead of showing a dead link. */}
        {permissions === 'admin' && <Menu.ResourceItem name="users" />}
        {permissions === 'admin' && (
          <Menu.Item to="/api-keys" primaryText="API keys" leftIcon={<VpnKeyIcon />} />
        )}
      </CollapsibleSection>
    </Menu>
  );
};

const AppLayout = ({ children }) => (
  <Layout menu={AppMenu} appBar={ScopeAppBar}>
    {children}
  </Layout>
);

const App = () => (
  <Admin
    dataProvider={dataProvider}
    authProvider={authProvider}
    store={store}
    loginPage={LoginPage}
    dashboard={Dashboard}
    layout={AppLayout}
    theme={defaultLightTheme}
    darkTheme={defaultDarkTheme}
    defaultTheme="dark"
    title="Support Chat Admin"
    disableTelemetry
  >
    <Resource
      name="sessions"
      options={{ label: 'Conversations' }}
      list={ConversationList}
      show={ConversationShow}
      icon={ForumIcon}
    />
    <Resource
      name="unresolved"
      options={{ label: 'Escalations' }}
      list={EscalationList}
      icon={ReportProblemIcon}
    />
    <Resource
      name="kb"
      options={{ label: 'Knowledge base' }}
      list={KbList}
      edit={KbEdit}
      create={KbCreate}
      icon={LibraryBooksIcon}
    />
    <Resource
      name="kb_variables"
      options={{ label: 'KB variables' }}
      list={KbVariableList}
      edit={KbVariableEdit}
    />
    <Resource
      name="users"
      options={{ label: 'Users' }}
      list={UserList}
      edit={UserEdit}
      create={UserCreate}
      icon={PeopleIcon}
    />
    <CustomRoutes>
      <Route path="/prompt" element={<Prompt />} />
      {/* Legacy deep links from the previous menu layout. */}
      <Route path="/prompt-preview" element={<Navigate to="/prompt" replace />} />
      <Route
        path="/prompt-variables"
        element={<Navigate to="/prompt?tab=variables" replace />}
      />
      <Route path="/translations" element={<Translations />} />
      <Route path="/site-map" element={<SiteMap />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/structure" element={<Structure />} />
      <Route path="/retention" element={<Retention />} />
      <Route path="/retention-agent" element={<RetentionAgent />} />
      {/* Legacy bookmark: the old Retention v2 path lands on the agent page. */}
      <Route path="/retention-v2" element={<RetentionAgent />} />
      <Route path="/api-keys" element={<ApiKeys />} />
    </CustomRoutes>
  </Admin>
);

export default App;
