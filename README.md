<h1 align="center">
   <!-- delameter/vkimexp -->
   <a href="##"><img align="left" src="https://s3.eu-north-1.amazonaws.com/dp2.dl/projects/delameter/vkimexp/logo.png" width="96" height="96"></a>
   <a href="##"><img align="center" src="https://s3.eu-north-1.amazonaws.com/dp2.dl/projects/delameter/vkimexp/label.png" width="200" height="64"></a>
</h1>
<div align="right">
  <a href="##"><img src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white&labelColor=333333"></a>
  <a href="https://pepy.tech/project/vkimexp/"><img alt="Downloads" src="https://pepy.tech/badge/vkimexp"></a>
  <a href="https://pypi.org/project/vkimexp/"><img alt="PyPI" src="https://img.shields.io/pypi/v/vkimexp"></a>
  <a href="https://github.com/psf/black"><img alt="Code style: black" src="https://img.shields.io/badge/code%20style-black-000000.svg"></a>
</div>
<br>

VK conversations exporter.

## Motivation

Necessity to backup VK conversations together with attachments, not just the text content (that's what official data export tool does).


## Installation

    pipx install vkimexp


## Usage

    vkimexp [OPTIONS] PEERS...

PEER should be VK ID of a person or a conversation in question (several PEERs can be provided at once). To find PEER of a person, open this page: https://vk.com/im and select the required dialog, and then his/her VK ID will appear in the address bar like this:

    https://vk.com/im?sel=1234567890                                               

where 1234567890 is a numeric ID in question. Use this number as PEER, e.g. for a person with VK ID 1234567890 the command is:

    vkimexp 1234567890                                                             

For group conversations there is no VK ID in the URL, as they are identified differently, by their index. Nevertheless, take this number (together with 'c'!) and provide it as is, the application will figure out VK ID of a conversation by itself:

    https://vk.com/im?sel=c195  =>  vkimexp c195                                   

### Options

    -b, --browser NAME  Browser to use cookies from (process is automatic).            
                        [default: chrome]                                              
    -v, --verbose       [0<=x<=1]                                                      
    --help              Show this message and exit.


### Running

![example-run.png](example-run.png)

### Result 

![example-result.png](example-result.png)

## Troubleshooting

#### Cannot authenticate the app on Ubuntu systems

One possible solution is to set up an environment variable ```XDG_CURRENT_DESKTOP=GNOME``` so that the cookie extraction code can correctly identify the system keyring and, subsequently, the browser to take them from.
