export default {
    extends: ["stylelint-config-recommended"],
    ignoreFiles: [
        "src/finance_app/static/vendor/**/*.css",
    ],
    rules: {
        "declaration-block-no-shorthand-property-overrides": null,
        "no-descending-specificity": null,
    },
};
