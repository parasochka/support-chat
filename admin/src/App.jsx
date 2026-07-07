import { useState } from 'react';
import {
  Admin,
  CustomRoutes,
  Layout,
  Menu,
  Resource,
  defaultDarkTheme,
  defaultLightTheme,
} from 'react-admin';
import { Navigate, Route, useLocation, useNavigate } from 'react-router-dom';
import Box from '@mui/material/Box';
import Collapse from '@mui/material/Collapse';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import ForumIcon from '@mui/icons-material/Forum';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import InsightsIcon from '@mui/icons-material/Insights';
import LibraryBooksIcon from '@mui/icons-material/LibraryBooks';
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
import Dashboard from './dashboard/Dashboard';
import ScopeAppBar from './layout/ScopeAppBar';
import { ConversationList, ConversationShow } from './resources/Conversations';
import { EscalationList } from './resources/Escalations';
import { KbCreate, KbEdit, KbList } from './resources/KnowledgeBase';
import { KbVariableEdit, KbVariableList } from './resources/KbVariables';
import { UserCreate, UserEdit, UserList } from './resources/Users';
import Prompt from './pages/Prompt';
import Retention from './pages/Retention';
import Settings from './pages/Settings';
import Structure from './pages/Structure';
import Translations from './pages/Translations';

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
      <ListItemButton onClick={toggle} sx={{ px: 2, py: 0.5 }}>
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

// A retention sub-tab as its own sidebar entry: navigates to /retention?tab=…
// and highlights when that tab is the active one (the page reads ?tab=).
const RetentionSubItem = ({ tab, label, icon }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const active =
    location.pathname.startsWith('/retention') &&
    (new URLSearchParams(location.search).get('tab') || 'config') === tab;
  return (
    <ListItemButton
      selected={active}
      onClick={() => navigate(`/retention?tab=${tab}`)}
      sx={{ pl: 4, py: 0.4 }}
    >
      <ListItemIcon sx={{ minWidth: 34 }}>{icon}</ListItemIcon>
      <ListItemText primary={label} slotProps={{ primary: { variant: 'body2' } }} />
    </ListItemButton>
  );
};

/**
 * The sidebar in three collapsible sections: the support-chat surface, the
 * Telegram retention bot (whose sub-tabs are exposed as sub-menu entries), and
 * system-wide management. Section open/closed state is remembered. KB variables
 * ride inside the Knowledge base page (a tab there), so they get no menu item.
 */
const AppMenu = () => (
  // Icons in the sidebar come from three different sources (resource items,
  // custom Menu.Items, retention sub-items with fontSize="small") — normalize
  // every one of them to the same size so the menu reads as one column.
  <Menu
    sx={{
      '& .MuiListItemIcon-root .MuiSvgIcon-root': { fontSize: 20 },
      '& .MuiListItemIcon-root': { minWidth: 34 },
    }}
  >
    <Menu.DashboardItem />

    <CollapsibleSection id="support" label="Support chat">
      <Menu.ResourceItem name="sessions" />
      <Menu.ResourceItem name="unresolved" />
      <Menu.ResourceItem name="kb" />
      <Menu.Item to="/prompt" primaryText="Prompt" leftIcon={<TuneIcon />} />
      <Menu.Item
        to="/translations"
        primaryText="Translations"
        leftIcon={<TranslateIcon />}
      />
    </CollapsibleSection>

    <CollapsibleSection id="retention" label="Telegram · Retention">
      <RetentionSubItem tab="guide" label="Setup guide" icon={<MenuBookIcon fontSize="small" />} />
      <RetentionSubItem tab="config" label="Telegram config" icon={<TelegramIcon fontSize="small" />} />
      <RetentionSubItem tab="kb" label="Retention KB" icon={<LibraryBooksIcon fontSize="small" />} />
      <RetentionSubItem tab="prompt" label="Prompt preview" icon={<TuneIcon fontSize="small" />} />
      <RetentionSubItem tab="photos" label="Media" icon={<PhotoLibraryIcon fontSize="small" />} />
      <RetentionSubItem tab="managers" label="Managers" icon={<SupportAgentIcon fontSize="small" />} />
      <RetentionSubItem tab="chats" label="Conversations" icon={<ForumIcon fontSize="small" />} />
      <RetentionSubItem tab="analytics" label="Analytics" icon={<InsightsIcon fontSize="small" />} />
    </CollapsibleSection>

    <CollapsibleSection id="system" label="System">
      <Menu.Item to="/structure" primaryText="Structure" leftIcon={<AccountTreeIcon />} />
      <Menu.Item to="/settings" primaryText="Settings" leftIcon={<SettingsIcon />} />
      <Menu.ResourceItem name="users" />
    </CollapsibleSection>
  </Menu>
);

const AppLayout = ({ children }) => (
  <Layout menu={AppMenu} appBar={ScopeAppBar}>
    {children}
  </Layout>
);

const App = () => (
  <Admin
    dataProvider={dataProvider}
    authProvider={authProvider}
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
      <Route path="/settings" element={<Settings />} />
      <Route path="/structure" element={<Structure />} />
      <Route path="/retention" element={<Retention />} />
    </CustomRoutes>
  </Admin>
);

export default App;
