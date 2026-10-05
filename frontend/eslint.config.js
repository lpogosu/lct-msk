import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'src/api/schema.d.ts', 'src/api/openapi.json'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports', fixStyle: 'inline-type-imports' }],
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
  {
    // В дизайн-системе рядом с компонентом живут его хелперы (heatLevel, buttonClasses, useToast…) — это осознанно
    files: ['src/ui/**/*.{ts,tsx}'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    // Node-скрипты (gen:api, optimize:3d)
    files: ['scripts/**/*.mjs', '*.config.js'],
    languageOptions: { ecmaVersion: 2023, globals: globals.node },
    extends: [js.configs.recommended],
  },
)
