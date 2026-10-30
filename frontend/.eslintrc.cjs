module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: ['eslint:recommended', 'plugin:react/recommended', 'plugin:react-hooks/recommended'],
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
  settings: { react: { version: 'detect' } },
  plugins: ['react', 'react-hooks'],
  ignorePatterns: ['dist', 'node_modules', 'coverage'],
  overrides: [
    {
      // Test files and setup run under Vitest, where `globalThis` is the
      // portable spelling and `global` is not defined.
      files: ['**/*.test.js', '**/*.test.jsx', 'src/test/**'],
      env: { node: true, browser: true },
      globals: { globalThis: 'readonly', vi: 'readonly' },
    },
  ],
  rules: {
    'react/prop-types': 'off',
    // The automatic JSX runtime (React 17+) does not require React in scope.
    'react/react-in-jsx-scope': 'off',
    'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
  },
}
