module Version

  def self.inject_variable(filename, flag, value)
    result = system 'python', 'tasks/version/inject_variable.py', flag, value, filename
    raise (lowlight("Version injection failed")) if not result
  end

  def self.inject_git_hash
    inject_variable(VERSION_FILE, '-g', get_git_hash)
  end

  def self.inject_rpm_version
    rpm_version = "#{get_package_version}-#{get_rpm_build}"
    inject_variable(VERSION_FILE, '-r', rpm_version)
  end

  def self.inject_build_number
    inject_variable(VERSION_FILE, '-D', "__build__ #{get_jenkins_build}")
  end

  def self.get_package_version
    Setup.get_package_version
  end

  def self.get_git_hash
    Git.get_current_hash
  end

  # Reads the BUILD_NUMBER environment variable if available
  # Default to "dev1", which sorts "earlier" than all actual numbers
  # in python version comparisons
  def self.get_jenkins_build
    ENV['BUILD_NUMBER'] or "dev1"
  end

  # Reads the RPM_BUILD environment variable if available
  def self.get_rpm_build
    ENV['RPM_BUILD'] or raise(lowlight("RPM_BUILD is not set"))
  end

  VERSION_FILE = "#{ProjectPaths::PACKAGE_DIR}/version.py"
end

namespace "version" do
  desc "Inject the current git hash into #{Version::VERSION_FILE}"
  task :inject_git do
    Version.inject_git_hash
  end

  desc "Inject the current RPM version and build into #{Version::VERSION_FILE}"
  task :inject_rpm do
    Version.inject_rpm_version
  end

  desc "Inject the current build number into #{Version::VERSION_FILE}"
  task :inject_build_number do
    Version.inject_build_number
  end
end
