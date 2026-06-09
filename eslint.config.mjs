import js from "@eslint/js";
import globals from "globals";

const financeBrowserGlobals = {
    bootstrap: "readonly",
    CSS: "readonly",
    echarts: "readonly",
    escapeHtml: "readonly",
    financeFormatAxisMoney: "readonly",
    financeFormatMoney: "readonly",
    financeTranslate: "readonly",
    flatpickr: "readonly",
    getCsrfToken: "readonly",
    monthSelectPlugin: "readonly",
    selectDashboardDrilldownItem: "readonly",
    setupFlatpickrInputs: "readonly",
};

export default [
    {
        ignores: [
            ".venv/**",
            "docs/**",
            "node_modules/**",
            "runtime/**",
            "src/finance_app/static/vendor/**",
            "vibecoding/**",
        ],
    },
    {
        files: ["src/finance_app/static/js/**/*.js"],
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "script",
            globals: {
                ...globals.browser,
                ...financeBrowserGlobals,
            },
        },
        rules: {
            ...js.configs.recommended.rules,
            eqeqeq: ["error", "smart"],
            "no-unused-vars": [
                "error",
                {
                    argsIgnorePattern: "^_",
                    caughtErrorsIgnorePattern: "^_",
                    varsIgnorePattern: "^(_|escapeHtml|getCsrfToken)$",
                },
            ],
            "no-redeclare": ["error", { builtinGlobals: false }],
            "no-var": "error",
        },
    },
];
