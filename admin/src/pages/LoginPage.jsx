import { useState } from 'react';
import { Login, useLogin, useNotify } from 'react-admin';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Checkbox from '@mui/material/Checkbox';
import CircularProgress from '@mui/material/CircularProgress';
import FormControlLabel from '@mui/material/FormControlLabel';
import TextField from '@mui/material/TextField';
import { rememberDefault } from '../session';

// Named-account login (email + password) with a "Remember me" box. Ticked, the
// session is stored in localStorage and survives a browser restart; unticked,
// it lives in sessionStorage and is dropped when the browser closes. The choice
// is remembered so the box comes back the way the operator left it.
const LoginForm = () => {
  const login = useLogin();
  const notify = useNotify();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(rememberDefault);
  const [loading, setLoading] = useState(false);

  const submit = (e) => {
    e.preventDefault();
    setLoading(true);
    login({ username: email, password, remember })
      .catch((err) =>
        notify(err?.message || 'Invalid email or password', { type: 'error' })
      )
      .finally(() => setLoading(false));
  };

  return (
    <Box component="form" onSubmit={submit} sx={{ p: 2, width: 300 }}>
      <TextField
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        fullWidth
        margin="normal"
        autoFocus
        autoComplete="username"
        disabled={loading}
      />
      <TextField
        label="Password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        fullWidth
        margin="normal"
        autoComplete="current-password"
        disabled={loading}
      />
      <FormControlLabel
        control={
          <Checkbox
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            disabled={loading}
          />
        }
        label="Remember me"
      />
      <Button
        type="submit"
        variant="contained"
        fullWidth
        disabled={loading}
        sx={{ mt: 1 }}
      >
        {loading ? <CircularProgress size={20} /> : 'Sign in'}
      </Button>
    </Box>
  );
};

const LoginPage = () => (
  <Login>
    <LoginForm />
  </Login>
);

export default LoginPage;
