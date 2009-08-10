#!/usr/bin/perl
#
# Loathsxome
# http://toroid.org/loathsxome
#
# Copyright 2009 Abhijit Menon-Sen <ams@toroid.org>
#
# Based on Blosxom 2.0, by Rael Dornfest <rael@oreilly.com>
# Portions from Blosxom 2.1.2, http://blosxom.sourceforge.net
#

package loathsxome;

# --- Configuration ---

$name = "Pretensions to eloquence";
$description = "Chronicles of despotism and debauchery";
$language = "en";
$url = "http://example.com/despot/journal";

$datadir = "/home/despot/loathsxome";
$plugindir = "$datadir/plugins";
$statedir = "$plugindir/state";
$extension = "post";
$depth = 0;

$flavour = "html";
@flavours = qw(html rss);

# --- No user-serviceable parts below this line ---

use vars qw(
    $name $description $language $url $datadir $plugindir $statedir $extension
    $depth $flavour @flavours @plugins %filters $path_info $post $entries $sort
    %entries @entries %template $template $interpolate $content_type $output
    $title $body $startnum $endnum $fn $datetime $gmtoff $postlink $rsslink
    $version
);

use strict;
use CGI qw/:standard/;
use File::Find;
use File::stat;
use POSIX qw/difftime mktime/;

$version = "0.99";

$url ||= url();

$gmtoff = difftime(mktime(localtime), mktime(gmtime));

$rsslink = "$url/index.rss";

map { s/\/$// } $url, $datadir, $plugindir, $statedir;

# Adjust $depth to take into account $datadir's path
$depth += ($datadir =~ y,/,,) - 1 if $depth;

# Load plugins
#
# We load each normal file from the plugin directory (ignoring any whose
# name ends in an underscore; thus plugins may be temporarily disabled),
# then call start() on the loathsxome:: package defined by the file. If
# the function returns true, the plugin is recorded as being enabled.

if ($plugindir and opendir PLUGINS, $plugindir) {
    my @files =
        grep { /^\w+$/ && !/_$/ && -f "$plugindir/$_"  }
            sort readdir(PLUGINS);

    foreach my $plugin (@files) {
        eval { require "$plugindir/$plugin" };
        if ($@) {
            warn "error loading plugin $plugin: $@";
            next;
        }
        (my $name = $plugin) =~ s/^\d+//;
        $name = "loathsxome::$name";
        if ($name->can('start') && $name->start()) {
            push @plugins, $name;
        }
    }

    closedir PLUGINS;
}

# Parse PATH_INFO to determine what to display, and how
#
# The core does very little parsing. It knows how to display all entries
# in a given flavour, or a single entry. All filtering is implemented by
# plugins, which may add to %filters (to be acted on later).
#
# Each stage of the parsing must consume whatever part of $path_info it
# wishes; anything left over at the end is treated as an error.
#
# PATH_INFO = [ "/" ( post | index ) ]
# post = ( path-component "/" )* path-component
# index = "index." flavour
# path-component = [\w\d-.]+
# flavour = [a-z]+
#

($path_info = path_info()) =~ s/^\/*|\/*$//;

if (-f "$datadir/$path_info.$extension") {
    $post = "$path_info.$extension";
    $path_info = "";
}

run_plugins('parse_uri');

$flavour ||= "html";
my $flavours = join '|', @flavours;
if ($path_info =~ s/^index\.($flavours)$//) {
    $flavour = $1;
}

not_found() if $path_info;

# Find entries
#
# The core defines a default listing function that looks for files with
# $extension to the given $depth under $datadir. This may be overriden
# by the first plugin that returns a coderef from entries().
#
# The listing function is expected to return a hash that maps between
# filenames relative to $datadir and a hash where each entry represents
# a property of the file (such as its mtime).
#
# Note that we load (and filter, and sort) all available entries even if
# we are going to display only a single post. This is primarily so that
# a plugin can generate things like appropriate next/previous links for
# inclusion in the post display.

$entries = sub {
    my %files;

    find(sub {
        my $d;
        my $curr_depth = $File::Find::dir =~ tr[/][];
        return if $depth and $curr_depth > $depth;

        my $file = $File::Find::name;
        if ($file =~ m{^$datadir/.*\.$extension$} and -r $file) {
            my $s = stat($file) or return;
            $file =~ s/^$datadir\///;
            $files{$file} = { mtime => $s->mtime };
        }
    }, $datadir);

    return %files;
};
$entries = from_first_plugin('entries') || $entries;
%entries = $entries->();

# Filter the entries
#
# The core does no filtering, but plugins are free to modify %entries as
# they wish. Upon completion, it is assumed that any filter expressions
# specified in the URL have been applied to %entries.

run_plugins('filter');

not_found() unless keys %entries;

# Sort the entries for display
#
# We define a default sorting function that sorts entries in descending
# order of their mtime. The first plugin to return a coderef from sort()
# may override this. A sorting function is expected to return an array
# of filenames (keys in %entries).

$sort = sub {
    sort { $entries{$b}{mtime} <=> $entries{$a}{mtime} }
        keys %entries;
};
$sort = from_first_plugin('sort') || $sort;
@entries = $sort->();

# Prepare templates for display
#
# A template function is called with the path to a post (or an empty
# string if we are meant to display more than one post), the name of a
# template component, and a desired flavour. It must return the text of
# the specified component.
#
# The default template function walks backwards through the path looking
# for files named "component.flavour". If no such file is found, it uses
# the built-in value for that component, if known, or an empty string.
# The first plugin to return a coderef from template() may override this
# behaviour.
#
# The template function is called five times (as many times as there are
# components: content_type, head, date, story, foot) for each post we
# must render.
#
# We also define a default interpolation function that takes a string as
# an argument, substitutes values of $variables, and returns a string.
# This function may also be overriden by the first plugin that returns a
# coderef from interpolate(). The interpolation function is called once
# for each template component rendered.

while (<DATA>) {
    chomp;
    last if /^__END__$/;
    next unless my ($type, $comp, $text) = /^(\S+)\s(\S+)(?:\s(.*))?$/;
    $text =~ s/\\n/\n/mg;
    $template{$type}{$comp} .= $text . "\n";
}

$template = sub {
    my ($path, $component, $flavour) = @_;

    do {
        $path =~ s/\/?[^\/]+$//;

        my $dir = $datadir;
        $dir .= "/$path" if $path;
        if (open(my $fh, "$dir/$component.$flavour")) {
            return join '', <$fh>
        }
    }
    while ($path);

    return $template{$flavour}{$component} || '';
};
$template = from_first_plugin('template') || $template;

$interpolate = sub {
    package loathsxome;
    (my $template = shift) =~
        s/(\$\w+(?:::)?\w*)/"defined $1 ? $1 : ''"/gee;
        # s/(\$\w+(?:::\w+)*(?:(?:->)?{(['"]?)[-\w]+\2})?)/"defined $1 ? $1 : ''"/gee;
    return $template;
};
$interpolate = from_first_plugin('interpolate') || $interpolate;

# If we're going to display only one post, we'll load its contents
# early, so that plugin functions have access to them.

if ($post and open(my $fh, "$datadir/$post")) {
    read_contents($post, $fh);
}

# Generate output

$content_type = $template->($post, 'content_type', $flavour);
$content_type =~ s/\n.*$//s;

print header(-type => $content_type);
{
    # First, generate the page header
    my $head = $template->($post, 'head', $flavour);
    run_plugins('head', $post, \$head);
    $head = $interpolate->($head);
    $output .= $head;

    # Then generate one or more entries
    my $n = 0;
    my $curdate;
    foreach my $entry (@entries) {
        $n++;

        # If we are to display a single post, or a range of posts, we
        # skip entries outside the desired range.
        if (($post and $entry ne $post) or
            ($startnum and $n < $startnum) or ($endnum and $n > $endnum))
        {
            next;
        }

        ($fn = $entry) =~ s/\.$extension//;
        $postlink = "$url/$fn";

        # Load file contents (unless already loaded)
        if (not exists $entries{$entry}{title} && -f "$datadir/$entry") {
            if (open(my $fh, "$datadir/$entry")) {
                read_contents($entry, $fh);
            }
        }
        $title = $entries{$entry}{title};
        $body = $entries{$entry}{body};

        # Generate a formatted date (each time we encounter a new one)
        my $date = $template->($post, 'date', $flavour);
        run_plugins('date', $entry, \$date);
        $date = $interpolate->($date);

        if ($date && $curdate ne $date) {
            $curdate = $date;
            $output .= $curdate;
        }

        # Read and process the contents of this entry
        my $story = $template->($post, 'story', $flavour);
        run_plugins('story', $entry, $fn, \$story, \$title, \$body);

        # Entity encoding
        if ($content_type =~ /\bxml\b/ &&
            $content_type !~ /\bxhtml\b/)
        {
            my %escape = (
                '<' => '&lt;', '>' => '&gt;', '&' => '&amp;',
                '"' => '&quot;', "'" => '&apos;'
            );

            my $uesc_re = qr([^-/a-zA-Z0-9:._]);
            $url   =~ s($uesc_re)(sprintf('%%%02X', ord($&)))eg;
            $fn    =~ s($uesc_re)(sprintf('%%%02X', ord($&)))eg;

            my $hesc_re = join '|' => keys %escape;
            $title =~ s/($hesc_re)/$escape{$1}/g;
            $body  =~ s/($hesc_re)/$escape{$1}/g;
            $url   =~ s/($hesc_re)/$escape{$1}/g;
            $fn    =~ s/($hesc_re)/$escape{$1}/g;
        }

        $story = $interpolate->($story);
        $output .= $story;
    }

    # Now the footer
    my $foot = $template->($post, 'foot', $flavour);
    run_plugins('foot', $post, \$foot);
    $foot = $interpolate->($foot);
    $output .= $foot;

    run_plugins('last');
    run_plugins('end');
}
print $output;

sub from_first_plugin {
    my ($name) = @_;

    my $val;
    foreach my $plugin (@plugins) {
        if ( $plugin->can($name) ) {
            last if defined($val = $plugin->$name());
        }
    }

    return $val;
}

sub run_plugins {
    my ($name, @args) = @_;

    foreach my $plugin (@plugins) {
        if ( $plugin->can($name) ) {
            $plugin->$name(@args);
        }
    }
}

sub read_contents {
    my ($file, $fh) = @_;

    if (defined $fh) {
        chomp($entries{$file}{title} = <$fh>);
        chomp($entries{$file}{body} = join '', <$fh>);
    }
}

sub not_found {
    print "Status: 404 Not found\r\n";
    print "Content-Type: text/plain\r\n\r\n";
    print "Not found.\r\n";
    exit;
}

__DATA__
html content_type text/html; charset=utf-8
html head <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">
html head <html><head>
html head <link rel=alternate type="application/rss+xml" title=RSS href="$rsslink">
html head <title>$pagetitle</title>
html head </head><body>
html head <h1>$heading</h1>
html date <h2>$fulldate</h2>
html story <h3><a href="$postlink">$title</a></h3>
html story $body
html story <p>
html story $datetime
html story [$taglinks]
html story <a href="$postlink">link</a>
html story
html foot <p style="text-align: center">
html foot $newerposts $pagelist $olderposts
html foot </body></html>

rss content_type text/xml; charset=utf-8
rss head <?xml version="1.0" encoding="utf-8"?>
rss head <?xml-stylesheet title="CSS_formatting" type="text/css" href="http://www.interglacial.com/rss/rss.css"?>
rss head <!-- name="generator" content="loathsxome/$version" -->
rss head <!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD RSS 0.91//EN" "http://my.netscape.com/publish/formats/rss-0.91.dtd">
rss head <rss version="0.91"><channel>
rss head <link>$url</link><title>$pagetitle</title>
rss head <description>$description</description><language>$language</language>
rss story <item><title>$title</title><link>$postlink</link><description>$body</description></item>
rss foot </channel></rss>
rss date
__END__
