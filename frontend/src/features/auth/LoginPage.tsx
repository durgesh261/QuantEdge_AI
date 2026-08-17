import { useState, useEffect } from 'react';
import { isAuthenticated, login, logout } from '../../services/api';
import { useNavigate } from 'react-router-dom';

const LoginPage = () => {
  const [authToken, setAuthToken] = useState<string>('');
  const [loginError, setLoginError] = useState<string | null>(null);
  const [showError, setShowError] = useState(false);
  const navigate = useNavigate();

  // Check if already authenticated on page load
  useEffect(() => {
    if (isAuthenticated()) {
      // Already has a valid session cookie; redirect to dashboard
      navigate('/dashboard');
    }
  }, [navigate]);

  const handleLogin = async () => {
    setLoginError(null);
    setShowError(false);

    if (!authToken.trim()) {
      setLoginError('Please enter the authentication token.');
      setShowError(true);
      return;
    }

    const result = await login(authToken);

    if (result.success) {
      // Login successful - cookie set by backend, interceptor handles auth
      navigate('/dashboard');
    } else {
      setLoginError(result.error || 'Login failed. Please check your token and try again.');
      setShowError(true);
    }
  };

  const handleLogout = async () => {
    const result = await logout();

    if (result.success) {
      // Stay on login page after logout
      setAuthToken('');
      setLoginError(null);
    }
  };

  // If already authenticated (cookie from previous session), redirect immediately
  if (isAuthenticated() && !authToken) {
    navigate('/dashboard');
    return null;
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-8">
        <h2 className="text-2xl font-bold text-gray-900 text-center mb-6">
          Authentication
        </h2>

        {showError && loginError && (
          <div className="mb-4 p-3 rounded bg-red-100 text-red-800">
            {loginError}
          </div>
        )}

        <form className="space-y-4" onSubmit={e => {
          e.preventDefault();
          handleLogin();
        }}>
          <div>
            <label className="block text-gray-700 text-sm font-medium mb-2">
              Authentication Token
            </label>
            <input
              type="password"
              value={authToken}
              onChange={e => setAuthToken(e.target.value)}
              className="w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              placeholder="Enter your AUTH_TOKEN"
              required
            />
          </div>

          <button
            type="submit"
            className="w-full bg-blue-600 text-white font-medium py-2 rounded hover:bg-blue-700"
          >
            Log In
          </button>
        </form>

        <div className="mt-6 text-center">
          <p className="text-gray-600 text-sm">
            The authentication token is your backend AUTH_TOKEN value.
            This is set via environment variable on your deployment and should
            not be committed to source control.
          </p>
        </div>

        {loginError && showError && (
          <button
            onClick={handleLogout}
            className="mt-3 text-sm text-blue-600 underline"
          >
            Log Out
          </button>
        )}
      </div>
    </div>
  );
};

export default LoginPage;