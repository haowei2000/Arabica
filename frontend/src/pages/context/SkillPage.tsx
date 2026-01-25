export default function SkillPage() {
    return (
        <div className="text-center py-12">
            <div className="text-secondary-400 dark:text-secondary-600 mb-4">
                <svg
                    className="mx-auto h-12 w-12"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                >
                    <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M13 10V3L4 14h7v7l9-11h-7z"
                    />
                </svg>
            </div>
            <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
                Skills
            </h3>
            <p className="text-secondary-500 dark:text-secondary-400">
                Skill management coming soon...
            </p>
        </div>
    );
}
