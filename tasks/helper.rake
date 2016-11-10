#-----------------------------------------------------
# Helper methods
#-----------------------------------------------------

#
# highlight, lowlight, and yellowlight
# return strings -- messages with boxes around them,
# possibly colored.
#
module BoxMessageSupport
  #
  # Supporting functions for making boxes
  #
  def self.colorize(message, color=:red)
    colors = { :red => "31",
               :green => "32",
               :yellow => "33",
               :blue => "34" }

    color_code = colors[color]
    lines = array_of_lines(message)
    lines.map { |line|
      "\e[#{color_code}m#{line}\e[0m"
    }
  end

  #
  # message can be a String, or an Array of strings.
  # returns an Array of strings
  #
  def self.array_of_lines(message)
    message.is_a?(String) ? [message] : message
  end

  #
  # Get the width of a message,
  # which may be a string or an array of strings
  #
  def self.max_line_width(message)
    lines = array_of_lines(message)
    lines.map {|l| l.length}.max
  end

  def self.color_message(message, color)
    lines = array_of_lines(message)
    raw_width = max_line_width(lines)
    to_print = if $stdout.isatty then colorize(lines, color) else lines end

    box_message(to_print, raw_width)
  end

  #
  # Insert a centered title line
  # above the rest of the given message
  #
  def self.title(text, message)
    lines = array_of_lines(message)
    title_width = text.length
    message_width = max_line_width(lines)
    width = [title_width, message_width].max
    indent_width = (width - title_width) / 2
    indent_text = ' ' * indent_width
    title_line = indent_text + text
    [title_line] + lines
  end

  #
  # Message can be a string or an array of strings
  #
  def self.box_message(message, width=nil)
    message_lines = BoxMessageSupport.array_of_lines(message)
    message_width = BoxMessageSupport.max_line_width(message_lines)
    starred_message_lines = message_lines.map { |line|
      outdent = ' ' * (message_width - line.length)
      "* #{line}#{outdent} *"
    }

    # Form the box
    stars = '*' * ((width or message_width) + 4)
    header = ["", stars]
    footer = [stars, "", ""]
    lines = header + starred_message_lines + footer
    return lines.join("\n")
  end

end

module BoxMessage
  def box(message)
    BoxMessageSupport.box_message(message)
  end

  def highlight(message)
    BoxMessageSupport.color_message(message, :green)
  end

  def lowlight(message)
    BoxMessageSupport.color_message(BoxMessageSupport.title("FAILURE", message), :red)
  end

  def yellowlight(message)
    BoxMessageSupport.color_message(BoxMessageSupport.title("WARNING", message), :yellow)
  end
end

module Log
  #
  # notice, warn, and error print to the screen
  #
  include BoxMessage

  def error(message)
    fail(lowlight(message))
  end

  def warn(message)
    puts yellowlight(message)
  end

  def notice(message)
    puts box(message)
  end

  def good(message)
    puts highlight(message)
  end
end

module UserInput
  #
  # ask, ask_noecho, and confirm get user input
  #

  def ask message
    print message
    STDIN.gets.chomp
  end

  def ask_noecho message
    print message
    system "stty -echo"
    answer = STDIN.gets.chomp
    system "stty echo"
    answer
  end

  def confirm message
    result = ask(message + " [Y/N]")
    result[0,1].upcase == "Y"
  end
end

module EnvInfo
  #
  # These commands tell us about the environment
  #
  include Log

  def command_exists(command)
    cmd = `which #{command} 2>/dev/null`.strip
    $?.to_i == 0 and cmd != ''
  end

  def find_command(commands)
    commands.find {|c| command_exists(c)}
  end

  def require_env(varname, message)
    if ! ENV[varname] then
      error("#{varname} is not set" + message)
    end
  end
end

module Web
  def browse(url)
    browse_command = find_command([
      #Let freedesktop figure out how to open target
      'xdg-open',

      #Debian flavored distros have a special helper to open urls
      'x-www-browser',

      #Linux Desktop environment specific, these might ignore default browser settings
      'gnome-open',
      'kde-open',

      ENV['browser'],
      'open'
    ])
    if browse_command then
      sh "#{browse_command} '#{url}'" do |ok, res|
        if ! ok
          manual_browse(url)
        end
      end
    else
      manual_browse(url)
    end
  end

  def manual_browse(url)
      puts "Please open: #{url}"
  end
end

module PublishFiles
  include FileUtils
  SCP_REGEX = /[^\/]+\@[^\/]+\:.*/
  HTTP_REGEX = /^https?.*/
  #
  # Creates a temporary dir, passing the location to the given block.
  # When the block is complete, copies the contents of the temporary dir
  # into the destination, which may be a local directory, or an scp-like
  # destination of the form user@host:/some/path
  #
  def publish_files_to(destination)
    Dir.mktmpdir do |tmpdir|
      yield tmpdir if block_given?
      if SCP_REGEX =~ destination
        # Badass string quoting courtesy of
        # http://rors.org/2008/10/26/dont-escape-in-strings
        sh %|scp "#{tmpdir}"/* #{destination}| do |ok, res|
          error("Failed to upload to destination: #{destination}") unless ok
        end
      elsif HTTP_REGEX =~ destination
        # Assume this is a devpi index so use the devpi client
        args = "--index-url #{Project[:INDEX_URL]}"
        sh %|pip install #{args} "devpi-client>=2.5.0,<3"| do |ok, res|
          error("Couldn't install devpi client") unless ok
        end
        sh %|devpi use #{destination}| do |ok, res|
          error("Couldn't select devpi index: #{destination}") unless ok
        end
        sh %|devpi login #{Project[:DEVPI_USER]} --password #{Project[:DEVPI_PASSWORD]}| do |ok, res|
          error("Couldn't login to devpi as #{Project[:DEVPI_USER]}") unless ok
        end
        Dir.glob("#{tmpdir}/*") do |filename|
          sh %|devpi upload #{filename}| do |ok, res|
            error("Couldn't upload #{File.basename(filename)} to devpi index #{destination}") unless ok
          end
        end
      else
        # This funny invocation is apparently the recommended way to copy the entire
        # contents (but not the directory itself) of tmpdir into destination:
        #
        # http://www.ruby-doc.org/stdlib-1.9.3/libdoc/fileutils/rdoc/FileUtils.html#method-c-cp_r
        #
        cp_r("#{tmpdir}/.", destination)
      end
    end
  end
end

#
# Explicitly include FileUtils so the invocations of sh above don't fail
#
include FileUtils

include BoxMessage
include Log
include UserInput
include EnvInfo
include Web
include PublishFiles
