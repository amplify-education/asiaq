module Pip
  def self.install(opts)
    # make sure our pip is recent enough to return 0 on empty .pip files.
    sh "pip install 'pip>=1.1'" do |ok, status|
      error("pip failed") unless ok
    end
    # actually run the command
    args = []
    if Project[:INDEX_URL] then
      args += ["--index-url", Project[:INDEX_URL]]
    end
    if Project[:PYNEST_URL] then
      args += ["--find-links", Project[:PYNEST_URL]]
    end

    argstr = args.join(" ")

    sh "pip install #{argstr} #{opts}" do |ok, status|
      error("pip failed") unless ok
    end
  end
end
