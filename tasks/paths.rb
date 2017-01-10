module ProjectPaths
  EGG_BUILD_FILES = "dist/ build/ #{Project[:EGG]}.egg-info/*"
  PACKAGE_DIR = "#{Project[:EGG]}"
  TEST_DIR = "test"
  REPORTS_DIR = "reports"
  LINT_DIR = "#{TASKS_DIR}/lint"
  TEST_VIRTUALENV_DIR = "venv"
end
