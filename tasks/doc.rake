namespace :doc do
  desc 'Install the requirements for documentation'
  task :install => ["virtualenv:verify"] do
    notice("Install documentation dependencies")

    if File.exist?("docs/conf/requirements.txt")
        Pip.install "-r docs/conf/requirements.txt"
    else
        # get rid of this once everything is converted to using
        # the standard dependency filename requirements.txt
        Pip.install "-r docs/conf/doc-requirements.pip"
    end

    notice("Documentation dependencies installed")
    puts Setup.installed_versions
  end

  desc 'Create the package documentation'
  task :create => ["doc:install", "setup:develop"] do
    notice("Creating documentation")
    sh "sphinx-apidoc -f --no-toc -o docs/source #{ProjectPaths::PACKAGE_DIR}"
    sh "sphinx-build -b html -c docs/conf docs/source docs/build"
    good("Documentation created in docs/build/")
  end

  desc 'Publish the documentation to the docs directory'
  task :publish => [:create] do
    notice("Publishing documentation")
    require_env("DOCUMENTATION_PACKAGE_BASE", " - this is required to build the documentation.")
    doc_dir = ENV['DOCUMENTATION_PACKAGE_BASE']
    rm_rf doc_dir
    mkdir_p File.dirname(doc_dir)
    cp_r("docs/build",doc_dir)
    good("Documentation published to #{doc_dir}")
  end
end

desc 'Create the package documentation'
task :doc => ['doc:create']
