module Nose
  UNIT_TEST_DIR = "#{ProjectPaths::TEST_DIR}/unit"
  UNIT_TEST_REPORT_PATH = "#{ProjectPaths::REPORTS_DIR}/nosetests.txt"
  INTEGRATION_TEST_DIR = "#{ProjectPaths::TEST_DIR}/integration"
  SYSTEM_TEST_DIR = "#{ProjectPaths::TEST_DIR}/system"
  FUNCTIONAL_TEST_DIR = "#{ProjectPaths::TEST_DIR}/functional"

  #
  # Invokes nosetests for the given test directory,
  # with any additional args.
  #
  # The block, if supplied, will be invoked with each line
  # of output from nose.
  #
  # Returns boolean indicating success or failure.
  #
  def self.nosetests(test_dir, args="")
    unit_test_vars = "BOTO_CONFIG=test/helpers/aws.config AWS_CONFIG_FILE=test/helpers/aws.config " +
        "AWS_PROFILE=unit_tests "  # use invalid AWS credentials so that actual AWS API calls fail
    unit_test_vars += "ASIAQ_CONFIG=sample_configuration "  # make sure we use our config for portability
    unit_test_params = "--processes=-1 --process-timeout=300 "  # run tests in parallel
    #
    # Why three --where arguments?
    #
    # The first --where sets the working directory to the egg root directory.
    # This helps normalize any file operations, e.g. loading of config files.
    #
    # Subsequent --where arguments add directories to the list of directories that
    # will be recursively scanned by nose for tests to run. This has two effects:
    #
    # 1. Tests are discovered and run. Hence, we supply the test directory
    #    (either unit tests or integration tests) as our third --where argument,
    #    so that we actually find the appropriate tests to run!
    #
    # 2. When packages and modules are scanned by nose, this also makes them known
    #    to the coverage module, so it can report on whether the modules were covered
    #    by the tests.
    #
    #    Without the --where=PACKAGE_DIR argument, the coverage module would only
    #    be aware of modules that are explicitly imported by tests.
    #
    #    Of course, we want coverage to tell us if there are modules that we *didn't*
    #    import in the tests! So, we include the PACKAGE_DIR in the list of directories
    #    for nose to scan.
    #
    # The fact that nose scans the package directory for tests has two undesirable
    # effects, however:
    #
    # 1. Any methods that *look* like test methods to nose will be run as tests.
    #    As a result, you need to be careful to avoid methods that begin with the name test_.
    #
    # 2. If you have any module-level code that runs on import (for example, Celery worker
    #    initialization), that code will run when nose scans and imports the package.
    #
    #    This might be a problem if that module-level code has side effects, or depends on
    #    preconditions like environment variables or config files.
    #
    #    To work around this, you can mark executable (chmod +x) any modules that perform such
    #    initialization. Be aware that if you do this, the module will no longer be included
    #    in nose's coverage report unless explicitly imported by a test, so do so gingerly.
    #
    command_args = [
      test_dir == Nose::UNIT_TEST_DIR ? unit_test_vars : "",
      "nosetests -s",
      test_dir == Nose::UNIT_TEST_DIR ? unit_test_params : "",
      "--where=#{Dir.pwd}",
      "--where=#{ProjectPaths::PACKAGE_DIR}",
      "--where=#{test_dir}",
      args,
      "2>&1"]
    # We want to run in a shell so we can combine stderr & stdout
    # Ruby lacks a good built-in command for capturing stdout, stderr, and the exit status.
    # The best option is 1.9's popen3, but I don't want the initial
    # command people will run in this template -- rake test -- to fail immediately
    # if run under Ruby 1.8. So, we do this.
    command = command_args.join(" ")
    print command + "\n"
    nose = IO.popen(command)
    loop do
      line = nose.gets
      break if line.nil?
      yield line if block_given?
    end
    nose.close
    return $?.to_i == 0
  end

  def self.test_requirements_file
    ['test-requirements.pip', 'test-requirements.txt'].find {|f|
        File.exist?(f)
    }
  end

  #
  # Returns a hash, mapping code unit => percent coverage.
  # The total coverage is reported under the unit "TOTAL".
  #
  # If the coverage file has not been written, this will fail.
  #
  def self.read_coverage
    coverage = {}
    File.open(Nose::UNIT_TEST_REPORT_PATH).each_line do |line|
      if line.match(/^([\w\\.]+).*\s+(\d+)%/)
        unit, percent = $1, $2
        coverage[unit] = Integer(percent)
      end
    end
    return coverage
  end
end

namespace "test" do
  task :all => [:lint, :unit, :cover]

  desc "Install test dependencies"
  task :install => ["virtualenv:verify", "setup:develop"] do
    notice("Installing test dependencies")
    requirements = Nose.test_requirements_file or error("No test-requirements file found")
    Pip.install "-r #{requirements}"
    notice("Test dependencies installed")
    print Setup.installed_versions
  end

  desc "Create test reports directory"
  directory ProjectPaths::REPORTS_DIR

  desc "Create test reports directory"
  task :mkdir => [ProjectPaths::REPORTS_DIR]

  desc "Prepare test environment"
  task :prepenv => ["setup:develop", "test:install", :mkdir]

  # Split integration testing into 2 steps, prep and run so that, in AWS,
  # prep can be done in phase 2 when external access to dependencies is still
  # available as opposed to test runtime in the upper environments.
  desc "Prepare and run integration tests"
  task :integration => [:prepenv, :run_integration]

  desc "Run integration tests"
  task :run_integration do
    # integration tests require some pre-existing AWS state such as credentials S3 buckets and IAM permissions,
    # so we'll need to work with real-project config files instead of the sample_config files
    require_env('ASIAQ_CONFIG', 'Set ASIAQ_CONFIG to the directory containing Asiaq configuration files')
    notice("Running integration tests")
    if Project[:IS_COMPONENT] then
      ok = Nose.nosetests(Nose::INTEGRATION_TEST_DIR) do |line|
        puts line
      end
      error("Integration tests failed") unless ok
      good("Integration tests passed, sweet!")
    else
      notice("Egg is not a component, so skipping integration tests")
    end
  end

  desc "Run functional tests"
  task :functional => [:prepenv] do
    notice("Running functional tests")
    if Project[:IS_COMPONENT] then
      ok = Nose.nosetests(Nose::FUNCTIONAL_TEST_DIR) do |line|
        puts line
      end
      if(!ok)
        error("Functional tests failed") 
        exit 1
      end
      good("Functional tests passed, sweet!")
      exit 0
    else
      notice("Egg is not a component, so skipping functional tests")
    end
  end

  desc "Run system tests"
  task :system => [:prepenv] do
    notice("Running system tests")
    if Project[:IS_COMPONENT] then
      ok = Nose.nosetests(Nose::SYSTEM_TEST_DIR) do |line|
        puts line
      end
      error("System tests failed") unless ok
      good("System tests passed, sweet!")
    else
      notice("Egg is not a component, so skipping system tests")
    end
  end

  desc "Run unit tests"
  task :unit => [:prepenv] do
    notice("Running unit tests")
    #
    # We want to write to stdout and a file at the same time, AND
    # get the status code from the nosetests invocation (so we can't just pipe to tee)
    #
    FileUtils.rm_f(Nose::UNIT_TEST_REPORT_PATH)
    ok = false
    File.open(Nose::UNIT_TEST_REPORT_PATH, 'w') do |f|
      ok = Nose.nosetests(Nose::UNIT_TEST_DIR) do |line|
        puts line
        f.puts(line)
      end
    end
    error("Unit tests failed") unless ok
    good("Unit tests passed, sweet!")
  end

  desc "Check coverage from last test run"
  task :cover => [:mkdir] do
    notice("Checking code coverage")
    total_coverage = Nose.read_coverage["TOTAL"]
    ok = total_coverage >= 65
    error("Coverage failed - only #{total_coverage}%. Time to write more tests.") unless ok
    good("Unit test code coverage is #{total_coverage}% - it'll do!")
  end
end

desc "Run unit tests"
task :test => ["test:all"]
